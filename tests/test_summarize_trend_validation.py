import csv
import tempfile
import unittest
from pathlib import Path

import summarize_trend_validation as s


class TrendValidationSummaryTests(unittest.TestCase):
    def sample_rows(self) -> list[dict[str, str]]:
        return [
            {
                "pre_date": "20260530",
                "post_date": "20260531",
                "scope": "全体",
                "signal_type": "style",
                "signal": "前目脚質優勢",
                "predictive": "true",
                "pre_value": "42/109頭 39%",
                "post_value": "47/116頭 41%",
                "verdict": "再現",
            },
            {
                "pre_date": "20260530",
                "post_date": "20260531",
                "scope": "東京 芝",
                "signal_type": "style",
                "signal": "前目脚質優勢",
                "predictive": "true",
                "pre_value": "8/22頭 36%",
                "post_value": "7/26頭 27%",
                "verdict": "一部",
            },
            {
                "pre_date": "20260530",
                "post_date": "20260531",
                "scope": "東京 芝",
                "signal_type": "frame",
                "signal": "内枠優勢",
                "predictive": "true",
                "pre_value": "7/24頭 29%",
                "post_value": "4/28頭 14%",
                "verdict": "未再現",
            },
            {
                "pre_date": "20260530",
                "post_date": "20260531",
                "scope": "全体",
                "signal_type": "final3f",
                "signal": "結果確認: 上がり3F上位多め",
                "predictive": "false",
                "pre_value": "41/72頭 57%",
                "post_value": "44/72頭 61%",
                "verdict": "再現",
            },
            {
                "pre_date": "20260530",
                "post_date": "20260531",
                "scope": "京都 芝",
                "signal_type": "bloodline",
                "signal": "父 モーリス",
                "predictive": "true",
                "pre_value": "4/5頭 80%",
                "post_value": "0/1頭 0%",
                "verdict": "判定不可",
            },
        ]

    def test_summarize_rows_splits_predictive_and_result_check(self):
        summaries = s.summarize_rows(self.sample_rows(), min_pairs=20, min_judged=20)
        by_key = {(row.group, row.signal_type): row for row in summaries}

        style = by_key[("予測信号", "style")]
        frame = by_key[("予測信号", "frame")]
        final3f = by_key[("結果確認", "final3f")]
        bloodline = by_key[("予測信号", "bloodline")]

        self.assertEqual(style.judged, 2)
        self.assertEqual(style.reproduced, 1)
        self.assertEqual(style.partial, 1)
        self.assertEqual(style.reproduction_rate, "50%")
        self.assertEqual(style.reproduced_or_partial_rate, "100%")
        self.assertIn("参考値", style.sample_note)
        self.assertEqual(frame.missed, 1)
        self.assertEqual(final3f.reproduced, 1)
        self.assertEqual(bloodline.unavailable, 1)
        self.assertEqual(bloodline.judged, 0)
        self.assertEqual(bloodline.reproduction_rate, "-")

    def test_outputs_markdown_and_csv(self):
        rows = self.sample_rows()
        summaries = s.summarize_rows(rows, min_pairs=1, min_judged=1)

        with tempfile.TemporaryDirectory() as tmp:
            md_path = Path(tmp) / "summary.md"
            csv_path = Path(tmp) / "summary.csv"

            s.write_markdown(md_path, s.build_markdown(rows, summaries, Path("trend_validation.csv")))
            s.write_summary_csv(csv_path, summaries)

            md = md_path.read_text(encoding="utf-8-sig")
            with csv_path.open("r", newline="", encoding="utf-8-sig") as f:
                csv_rows = list(csv.DictReader(f))

        self.assertIn("## 予測信号", md)
        self.assertIn("## 結果確認", md)
        self.assertIn("再現+一部率", md)
        self.assertTrue(any(row["signal_type"] == "style" and row["reproduced_or_partial_rate"] == "100%" for row in csv_rows))

    def test_conclusion_flags_small_sample_as_reference(self):
        rows = self.sample_rows()
        summaries = s.summarize_rows(rows, min_pairs=20, min_judged=20)
        md = s.build_markdown(rows, summaries, Path("trend_validation.csv"), min_pairs=20, min_judged=20)
        self.assertIn("率は参考値です", md)
        self.assertNotIn("サンプル目安（20組／20件）を満たします", md)

    def test_conclusion_treats_large_sample_as_usable(self):
        rows = self.sample_rows()
        summaries = s.summarize_rows(rows, min_pairs=1, min_judged=1)
        md = s.build_markdown(rows, summaries, Path("trend_validation.csv"), min_pairs=1, min_judged=1)
        self.assertIn("参考指標として扱えます", md)
        self.assertNotIn("率は参考値です。信頼度更新", md)

    def test_target_year_summary_paths_are_year_scoped(self):
        output_dir = Path("reports")

        self.assertEqual(
            s.default_summary_paths(output_dir),
            (
                output_dir / "trend_validation.csv",
                output_dir / "trend_validation_summary.md",
                output_dir / "trend_validation_summary.csv",
            ),
        )
        self.assertEqual(
            s.default_summary_paths(output_dir, 2026),
            (
                output_dir / "trend_validation_2026.csv",
                output_dir / "trend_validation_summary_2026.md",
                output_dir / "trend_validation_summary_2026.csv",
            ),
        )


if __name__ == "__main__":
    unittest.main()
