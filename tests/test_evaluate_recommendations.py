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


class HighBandAlertTests(unittest.TestCase):
    def _band(self, label, bet_count, wins, place_return):
        return e.RecommendationSummary(
            group="スコア帯",
            label=label,
            total=bet_count,
            evaluated=bet_count,
            bet_count=bet_count,
            wins=wins,
            top3=0,
            missing=0,
            win_return=0,
            place_return=place_return,
        )

    def test_alert_when_high_band_has_no_wins(self):
        summaries = [self._band("80-89点", 11, 0, 8360)]
        alerts = e.high_band_alerts(summaries)
        self.assertEqual(len(alerts), 1)
        self.assertIn("80-89点", alerts[0])
        self.assertIn("勝ち0", alerts[0])

    def test_alert_when_high_band_place_return_below_floor(self):
        summaries = [self._band("80-89点", 20, 3, 1400)]  # 複勝回収70%
        alerts = e.high_band_alerts(summaries)
        self.assertEqual(len(alerts), 1)
        self.assertIn("割れ", alerts[0])

    def test_no_alert_for_small_sample_or_healthy_band(self):
        small = [self._band("80-89点", 5, 0, 0)]
        healthy = [self._band("80-89点", 30, 6, 3300)]  # 複勝回収110%
        low_band = [self._band("62-69点", 200, 0, 0)]  # 監視対象外の帯
        self.assertEqual(e.high_band_alerts(small), [])
        self.assertEqual(e.high_band_alerts(healthy), [])
        self.assertEqual(e.high_band_alerts(low_band), [])


if __name__ == "__main__":
    unittest.main()
