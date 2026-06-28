import sqlite3
import unittest

import race_day_guard as g


class RaceDayGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(
            """
            CREATE TABLE races (
                race_year TEXT,
                race_month_day TEXT,
                track_code TEXT,
                race_num TEXT
            );
            """
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_race_count_detects_jra_program(self):
        self.conn.execute(
            "INSERT INTO races VALUES (?, ?, ?, ?)",
            ("2026", "0915", "05", "01"),
        )

        self.assertEqual(g.race_count(self.conn, "20260915"), 1)

    def test_race_count_ignores_non_jra_track(self):
        self.conn.execute(
            "INSERT INTO races VALUES (?, ?, ?, ?)",
            ("2026", "0915", "99", "01"),
        )

        self.assertEqual(g.race_count(self.conn, "20260915"), 0)


if __name__ == "__main__":
    unittest.main()
