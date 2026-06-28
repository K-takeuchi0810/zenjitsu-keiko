from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from collect_trends import TRACK_NAMES, display_date, load_config, normalize_date

NO_RACE_EXIT_CODE = 2


def race_count(conn: sqlite3.Connection, date_key: str) -> int:
    placeholders = ",".join("?" for _ in TRACK_NAMES)
    sql = f"""
        SELECT COUNT(*)
          FROM races
         WHERE race_year || race_month_day = ?
           AND track_code IN ({placeholders})
    """
    return int(conn.execute(sql, [date_key, *TRACK_NAMES.keys()]).fetchone()[0] or 0)


def main() -> int:
    config = load_config()
    parser = argparse.ArgumentParser(description="指定日にJRA開催番組がDBへ入っているか確認します")
    parser.add_argument("--date", default="today", help="確認日。YYYYMMDD / YYYY-MM-DD / today")
    parser.add_argument("--db", default=config["source_db"], help="keiba.db のパス")
    args = parser.parse_args()

    date_key = normalize_date(args.date)
    db_path = Path(args.db)
    if not db_path.exists():
        raise FileNotFoundError(f"DBが見つかりません: {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        count = race_count(conn, date_key)
    finally:
        conn.close()

    if count <= 0:
        print(f"{display_date(date_key)} の開催番組はDBにありません。集計をスキップします。")
        return NO_RACE_EXIT_CODE
    print(f"{display_date(date_key)} の開催番組を検出しました: {count}R")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
