import unittest

import evaluate_recommendations as e


class RecommendationSummaryTests(unittest.TestCase):
    def test_summarize_rows_counts_win_and_top3_by_score_band(self):
        rows = [
            {
                "score": "82",
                "trend_source": "東京 ダート",
                "result_status": "勝ち",
                "win": "true",
                "in_top3": "true",
                "win_return": "420",
                "place_return": "160",
            },
            {
                "score": "74",
                "trend_source": "東京 ダート",
                "result_status": "3着内",
                "win": "false",
                "in_top3": "true",
                "win_return": "0",
                "place_return": "210",
            },
            {
                "score": "65",
                "trend_source": "東京 ダート",
                "result_status": "圏外",
                "win": "false",
                "in_top3": "false",
                "win_return": "0",
                "place_return": "0",
            },
            {
                "score": "65",
                "trend_source": "東京 ダート",
                "result_status": "対象馬なし",
                "win": "false",
                "in_top3": "false",
                "win_return": "0",
                "place_return": "0",
            },
            {"score": "65", "trend_source": "東京 ダート", "result_status": "", "win": "", "in_top3": ""},
        ]

        summaries = e.summarize_rows(rows)
        overall = summaries[0]
        bands = {(row.group, row.label): row for row in summaries}

        self.assertEqual(overall.total, 5)
        self.assertEqual(overall.evaluated, 4)
        self.assertEqual(overall.bet_count, 3)
        self.assertEqual(overall.wins, 1)
        self.assertEqual(overall.top3, 2)
        self.assertEqual(overall.win_rate, "25%")
        self.assertEqual(overall.top3_rate, "50%")
        self.assertEqual(overall.win_return_rate, "140%")
        self.assertEqual(overall.place_return_rate, "123%")
        self.assertEqual(bands[("スコア帯", "80-89点")].win_rate, "100%")
        self.assertEqual(bands[("スコア帯", "62-69点")].evaluated, 2)
        self.assertEqual(bands[("スコア帯", "62-69点")].bet_count, 1)


if __name__ == "__main__":
    unittest.main()
