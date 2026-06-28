import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

import compare_previous_trends as v


class ValidationCsvTests(unittest.TestCase):
    def read_rows(self, path: Path) -> list[dict[str, str]]:
        with path.open("r", newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    def test_validation_csv_replaces_same_date_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trend_validation.csv"

            first_count = v.write_validation_csv(
                path,
                "20260530",
                "20260531",
                [
                    v.CheckResult("全体", "前目脚質優勢", "42/109頭 39%", "47/116頭 41%", "再現", True, "style"),
                    v.CheckResult("最終追い切り", "終い良好", "53%", "22%", "未再現", False, "training"),
                ],
            )
            first_rows = self.read_rows(path)

            self.assertEqual(first_count, 2)
            self.assertEqual(len(first_rows), 2)
            self.assertEqual(first_rows[0]["signal_type"], "style")
            self.assertEqual(first_rows[0]["predictive"], "true")
            self.assertEqual(first_rows[1]["signal_type"], "training")
            self.assertEqual(first_rows[1]["predictive"], "false")

            second_count = v.write_validation_csv(
                path,
                "20260530",
                "20260531",
                [
                    v.CheckResult("全体", "荒れ気味", "12/24R 50%", "11/24R 46%", "再現", True, "payout"),
                ],
            )
            second_rows = self.read_rows(path)

            self.assertEqual(second_count, 1)
            self.assertEqual(len(second_rows), 1)
            self.assertEqual(second_rows[0]["signal"], "荒れ気味")
            self.assertEqual(second_rows[0]["signal_type"], "payout")

            v.write_validation_csv(
                path,
                "20260531",
                "20260606",
                [
                    v.CheckResult("全体", "中枠優勢", "9/34頭 26%", "0/0頭 -", "判定不可", True, "frame"),
                ],
            )
            third_rows = self.read_rows(path)

            self.assertEqual(len(third_rows), 2)
            self.assertEqual({(row["pre_date"], row["post_date"]) for row in third_rows}, {
                ("20260530", "20260531"),
                ("20260531", "20260606"),
            })

    def test_latest_validation_pair_prefers_largest_post_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trend_validation.csv"
            v.write_validation_csv(
                path,
                "20260523",
                "20260524",
                [v.CheckResult("全体", "荒れ気味", "1", "2", "再現", True, "payout")],
            )
            v.write_validation_csv(
                path,
                "20260530",
                "20260531",
                [v.CheckResult("全体", "前目脚質優勢", "1", "2", "再現", True, "style")],
            )

            self.assertEqual(v.latest_validation_pair(path), ("20260530", "20260531"))


class LatestPairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE races (
                race_year TEXT,
                race_month_day TEXT,
                track_code TEXT,
                kaiji TEXT,
                nichiji TEXT,
                race_num TEXT
            );
            CREATE TABLE horse_races (
                race_year TEXT,
                race_month_day TEXT,
                track_code TEXT,
                kaiji TEXT,
                nichiji TEXT,
                race_num TEXT,
                confirmed_order INTEGER,
                finish_order INTEGER
            );
            """
        )

    def tearDown(self) -> None:
        self.conn.close()

    def insert_completed_date(self, date_key: str, races: int) -> None:
        year, month_day = date_key[:4], date_key[4:]
        for race_num in range(1, races + 1):
            params = (year, month_day, "05", "01", "01", f"{race_num:02d}")
            self.conn.execute("INSERT INTO races VALUES (?, ?, ?, ?, ?, ?)", params)
            for order in (1, 2, 3):
                self.conn.execute(
                    "INSERT INTO horse_races VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (*params, order, order),
                )

    def test_latest_completed_pair_uses_latest_two_full_dates(self):
        self.insert_completed_date("20260524", 24)
        self.insert_completed_date("20260530", 24)
        self.insert_completed_date("20260531", 24)
        self.insert_completed_date("20260601", 23)

        self.assertEqual(v.latest_completed_pair(self.conn, 24), ("20260530", "20260531"))

    def test_pending_validation_pairs_skip_existing_and_non_adjacent_dates(self):
        self.insert_completed_date("20260524", 24)
        self.insert_completed_date("20260525", 24)
        self.insert_completed_date("20260530", 24)
        self.insert_completed_date("20260531", 24)

        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "trend_validation.csv"
            v.write_validation_csv(
                csv_path,
                "20260530",
                "20260531",
                [v.CheckResult("全体", "荒れ気味", "12/24R 50%", "11/24R 46%", "再現", True, "payout")],
            )

            self.assertEqual(
                v.completed_adjacent_pairs(self.conn, 24, limit=10),
                [("20260524", "20260525"), ("20260530", "20260531")],
            )
            self.assertEqual(
                v.pending_validation_pairs(self.conn, csv_path, 24, limit=10),
                [("20260524", "20260525")],
            )

    def test_rolling_history_window_uses_previous_five_years(self):
        self.assertEqual(v.rolling_history_window(2026), ("20210101", "20251231"))
        self.assertEqual(v.rolling_history_window(2027), ("20220101", "20261231"))

    def test_target_year_validation_csv_path_is_year_scoped(self):
        reports_dir = Path("reports")

        self.assertEqual(v.default_validation_csv_path(reports_dir), reports_dir / "trend_validation.csv")
        self.assertEqual(v.default_validation_csv_path(reports_dir, 2026), reports_dir / "trend_validation_2026.csv")

    def test_rolling_history_pairs_filters_to_previous_year_window(self):
        self.insert_completed_date("20201226", 24)
        self.insert_completed_date("20210102", 24)
        self.insert_completed_date("20210103", 24)
        self.insert_completed_date("20251227", 24)
        self.insert_completed_date("20251228", 24)
        self.insert_completed_date("20260103", 24)

        self.assertEqual(
            v.rolling_history_pairs(self.conn, 2026, 24),
            [("20210102", "20210103"), ("20251227", "20251228")],
        )


if __name__ == "__main__":
    unittest.main()
