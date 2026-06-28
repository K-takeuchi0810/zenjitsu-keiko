import csv
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CollectTrendsE2ETests(unittest.TestCase):
    def create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE races (
                race_year TEXT,
                race_month_day TEXT,
                track_code TEXT,
                kaiji TEXT,
                nichiji TEXT,
                race_num TEXT,
                race_name TEXT,
                race_short10 TEXT,
                track_type_code TEXT,
                distance INTEGER,
                weather_code TEXT,
                turf_condition TEXT,
                dirt_condition TEXT,
                start_time TEXT
            );
            CREATE TABLE horse_races (
                race_year TEXT,
                race_month_day TEXT,
                track_code TEXT,
                kaiji TEXT,
                nichiji TEXT,
                race_num TEXT,
                horse_num TEXT,
                blood_register_num TEXT,
                waku_num INTEGER,
                horse_name TEXT,
                jockey_code TEXT,
                jockey_short_name TEXT,
                win_odds INTEGER,
                win_popularity INTEGER,
                leg_quality_code TEXT,
                abnormal_code TEXT,
                mining_predicted_order INTEGER,
                finish_order INTEGER,
                confirmed_order INTEGER,
                final_3f INTEGER
            );
            CREATE TABLE payouts (
                race_year TEXT,
                race_month_day TEXT,
                track_code TEXT,
                kaiji TEXT,
                nichiji TEXT,
                race_num TEXT,
                tan_payout1 INTEGER,
                umaren_payout1 INTEGER,
                sanrenpuku_payout1 INTEGER
            );
            CREATE TABLE horse_masters (
                blood_register_num TEXT,
                sire_name TEXT,
                dam_sire_name TEXT
            );
            CREATE TABLE offspring_master (
                blood_register_num TEXT,
                sire_name TEXT,
                dam_sire_name TEXT
            );
            """
        )

    def insert_race(self, conn: sqlite3.Connection, date_key: str, race_num: int, *, finished: bool) -> None:
        year, month_day = date_key[:4], date_key[4:]
        race_params = (year, month_day, "05", "01", "01", f"{race_num:02d}")
        conn.execute(
            """
            INSERT INTO races VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*race_params, f"E2E {race_num}R", f"{race_num}R", "23", 1200, "1", "0", "1", f"10{race_num:02d}"),
        )
        if finished:
            conn.execute(
                "INSERT INTO payouts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*race_params, 180, 620, 1400),
            )
        for num in range(1, 9):
            if finished:
                finish_order = num
                confirmed_order = num
                final_3f = 350 + num
                popularity = num
                odds = 20 + num
                style_code = "2" if num <= 3 else "3"
                mining_rank = 0
            else:
                finish_order = 0
                confirmed_order = 0
                final_3f = 0
                popularity = num
                odds = 20 + num
                style_code = "2" if num == 1 else "3"
                mining_rank = 1 if num == 1 else 0
            blood_num = f"{date_key}-{race_num:02d}-{num:02d}"
            conn.execute(
                """
                INSERT INTO horse_races VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    *race_params,
                    f"{num:02d}",
                    blood_num,
                    ((num - 1) % 8) + 1,
                    f"E2E馬{race_num}-{num}",
                    f"J{num}",
                    f"騎手{num}",
                    odds,
                    popularity,
                    style_code,
                    "0",
                    mining_rank,
                    finish_order,
                    confirmed_order,
                    final_3f,
                ),
            )
            conn.execute(
                "INSERT INTO horse_masters VALUES (?, ?, ?)",
                (blood_num, f"父{num}", f"母父{num}"),
            )

    def complete_target_race(self, conn: sqlite3.Connection, date_key: str) -> None:
        year, month_day = date_key[:4], date_key[4:]
        orders = {1: 2, 2: 1, 3: 3}
        for num in range(1, 9):
            order = orders.get(num, num)
            conn.execute(
                """
                UPDATE horse_races
                   SET finish_order = ?, confirmed_order = ?, final_3f = ?
                 WHERE race_year = ?
                   AND race_month_day = ?
                   AND track_code = '05'
                   AND race_num = '01'
                   AND horse_num = ?
                """,
                (order, order, 350 + num, year, month_day, f"{num:02d}"),
            )
        conn.execute(
            "INSERT INTO payouts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (year, month_day, "05", "01", "01", "01", 210, 880, 1900),
        )

    def run_collect(self, root: Path, db_path: Path, output_dir: Path, date_key: str, next_date: str | None = None) -> subprocess.CompletedProcess:
        cmd = [
            sys.executable,
            str(root / "collect_trends.py"),
            "--date",
            date_key,
            "--db",
            str(db_path),
            "--output-dir",
            str(output_dir),
            "--no-publish",
        ]
        if next_date:
            cmd.extend(["--next-date", next_date])
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        return subprocess.run(cmd, cwd=root, env=env, text=True, encoding="utf-8", capture_output=True, check=True)

    def read_csv_rows(self, path: Path) -> list[dict[str, str]]:
        with path.open("r", newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    def test_cli_generates_report_and_updates_recommendation_log(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "fixture.db"
            output_dir = tmp_path / "reports"
            conn = sqlite3.connect(db_path)
            try:
                self.create_schema(conn)
                for race_num in range(1, 4):
                    self.insert_race(conn, "20260530", race_num, finished=True)
                self.insert_race(conn, "20260531", 1, finished=False)
                conn.commit()
            finally:
                conn.close()

            first = self.run_collect(root, db_path, output_dir, "20260530", "20260531")

            report_dir = output_dir / "20260530"
            self.assertIn("おすすめ検証ログ", first.stdout)
            self.assertTrue((report_dir / "mobile.html").exists())
            self.assertTrue((report_dir / "trend.md").exists())
            self.assertTrue((report_dir / "races.csv").exists())
            self.assertIn("翌日おすすめ 前提データ", (report_dir / "trend.md").read_text(encoding="utf-8-sig"))
            mobile_html = (report_dir / "mobile.html").read_text(encoding="utf-8")
            self.assertIn('id="overview"', mobile_html)
            self.assertIn("<h2>概要</h2>", mobile_html)
            self.assertNotIn("全場合算傾向", mobile_html)
            self.assertNotIn('id="summary"', mobile_html)
            self.assertIn("前提データ", mobile_html)
            self.assertLess(mobile_html.index('id="overview"'), mobile_html.index('id="picks"'))
            self.assertLess(mobile_html.index('id="picks"'), mobile_html.index('id="track"'))

            race_rows = self.read_csv_rows(report_dir / "races.csv")
            self.assertEqual(len(race_rows), 3)

            log_path = output_dir / "recommendation_log.csv"
            log_rows = self.read_csv_rows(log_path)
            self.assertTrue(any(row["horse_num"] == "1" and row["target_date"] == "20260531" for row in log_rows))
            self.assertTrue(all(row["result_status"] == "" for row in log_rows))

            conn = sqlite3.connect(db_path)
            try:
                self.complete_target_race(conn, "20260531")
                conn.commit()
            finally:
                conn.close()

            second = self.run_collect(root, db_path, output_dir, "20260531")
            updated_rows = self.read_csv_rows(log_path)
            horse1 = next(row for row in updated_rows if row["horse_num"] == "1" and row["target_date"] == "20260531")

            self.assertIn("おすすめ結果反映", second.stdout)
            self.assertEqual(horse1["finish_order"], "2")
            self.assertEqual(horse1["result_status"], "3着内")
            self.assertEqual(horse1["in_top3"], "true")
            self.assertEqual(horse1["win"], "false")
