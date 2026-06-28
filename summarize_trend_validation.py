from __future__ import annotations

import argparse
import csv
import shutil
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from collect_trends import display_date, load_config, pct


SIGNAL_TYPE_LABELS = {
    "style": "脚質",
    "frame": "枠",
    "payout": "配当",
    "market": "人気",
    "bloodline": "血統",
    "final3f": "上がり3F",
    "training": "追い切り",
}
SIGNAL_TYPE_ORDER = ["style", "frame", "payout", "market", "bloodline", "final3f", "training"]
SUMMARY_CSV_FIELDS = [
    "group",
    "signal_type",
    "signal_label",
    "date_pairs",
    "total_rows",
    "judged",
    "reproduced",
    "partial",
    "missed",
    "unavailable",
    "reproduction_rate",
    "reproduced_or_partial_rate",
    "sample_note",
]


@dataclass
class SummaryRow:
    group: str
    signal_type: str
    signal_label: str
    date_pairs: int
    total_rows: int
    judged: int
    reproduced: int
    partial: int
    missed: int
    unavailable: int
    sample_note: str

    @property
    def reproduction_rate(self) -> str:
        return pct(self.reproduced, self.judged)

    @property
    def reproduced_or_partial_rate(self) -> str:
        return pct(self.reproduced + self.partial, self.judged)


def parse_bool(value: str) -> bool:
    return str(value or "").strip().lower() == "true"


def read_validation_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def date_pair(row: dict[str, str]) -> tuple[str, str]:
    return row.get("pre_date", ""), row.get("post_date", "")


def sample_note(date_pairs: int, judged: int, *, min_pairs: int, min_judged: int) -> str:
    if date_pairs < min_pairs:
        return f"参考値（検証{date_pairs}組。目安{min_pairs}組未満）"
    if judged < min_judged:
        return f"参考値（判定{judged}件。目安{min_judged}件未満）"
    return "通常"


def signal_sort_key(item: SummaryRow) -> tuple[int, int, str]:
    group_rank = 0 if item.group == "予測信号" else 1
    try:
        signal_rank = SIGNAL_TYPE_ORDER.index(item.signal_type)
    except ValueError:
        signal_rank = len(SIGNAL_TYPE_ORDER)
    return group_rank, signal_rank, item.signal_type


def summarize_rows(
    rows: list[dict[str, str]],
    *,
    min_pairs: int = 20,
    min_judged: int = 20,
) -> list[SummaryRow]:
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        group = "予測信号" if parse_bool(row.get("predictive", "")) else "結果確認"
        signal_type = row.get("signal_type", "") or "unknown"
        grouped[(group, signal_type)].append(row)

    summaries: list[SummaryRow] = []
    for (group, signal_type), group_rows in grouped.items():
        reproduced = sum(1 for row in group_rows if row.get("verdict") == "再現")
        partial = sum(1 for row in group_rows if row.get("verdict") == "一部")
        missed = sum(1 for row in group_rows if row.get("verdict") == "未再現")
        unavailable = sum(1 for row in group_rows if row.get("verdict") == "判定不可")
        judged = reproduced + partial + missed
        pairs = {date_pair(row) for row in group_rows if any(date_pair(row))}
        summaries.append(
            SummaryRow(
                group=group,
                signal_type=signal_type,
                signal_label=SIGNAL_TYPE_LABELS.get(signal_type, signal_type),
                date_pairs=len(pairs),
                total_rows=len(group_rows),
                judged=judged,
                reproduced=reproduced,
                partial=partial,
                missed=missed,
                unavailable=unavailable,
                sample_note=sample_note(len(pairs), judged, min_pairs=min_pairs, min_judged=min_judged),
            )
        )
    return sorted(summaries, key=signal_sort_key)


def summary_csv_rows(summaries: list[SummaryRow]) -> list[dict[str, str]]:
    return [
        {
            "group": row.group,
            "signal_type": row.signal_type,
            "signal_label": row.signal_label,
            "date_pairs": str(row.date_pairs),
            "total_rows": str(row.total_rows),
            "judged": str(row.judged),
            "reproduced": str(row.reproduced),
            "partial": str(row.partial),
            "missed": str(row.missed),
            "unavailable": str(row.unavailable),
            "reproduction_rate": row.reproduction_rate,
            "reproduced_or_partial_rate": row.reproduced_or_partial_rate,
            "sample_note": row.sample_note,
        }
        for row in summaries
    ]


def write_summary_csv(path: Path, summaries: list[SummaryRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(summary_csv_rows(summaries))


def markdown_table(lines: list[str], summaries: list[SummaryRow]) -> None:
    lines.append("|信号|検証組|総件数|判定件数|再現|一部|未再現|判定不可|再現率|再現+一部率|注記|")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for row in summaries:
        lines.append(
            f"|{row.signal_label}|{row.date_pairs}|{row.total_rows}|{row.judged}|"
            f"{row.reproduced}|{row.partial}|{row.missed}|{row.unavailable}|"
            f"{row.reproduction_rate}|{row.reproduced_or_partial_rate}|{row.sample_note}|"
        )


def build_markdown(
    rows: list[dict[str, str]],
    summaries: list[SummaryRow],
    input_path: Path,
    *,
    min_pairs: int = 20,
    min_judged: int = 20,
) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pairs = sorted({date_pair(row) for row in rows if any(date_pair(row))})
    pair_text = ", ".join(f"{display_date(pre)}->{display_date(post)}" for pre, post in pairs[:5])
    if len(pairs) > 5:
        pair_text += f" ほか{len(pairs) - 5}組"

    predictive = [row for row in summaries if row.group == "予測信号"]
    result_check = [row for row in summaries if row.group == "結果確認"]
    predictive_judged = sum(row.judged for row in predictive)
    predictive_reproduced = sum(row.reproduced for row in predictive)
    predictive_partial = sum(row.partial for row in predictive)

    lines = [
        "# 傾向検証 集計レポート",
        "",
        f"- 生成日時: {generated_at}",
        f"- 入力CSV: `{input_path}`",
        f"- 検証ペア数: {len(pairs)}",
        f"- 入力行数: {len(rows)}",
    ]
    if pair_text:
        lines.append(f"- 対象ペア: {pair_text}")
    sufficient = len(pairs) >= min_pairs and predictive_judged >= min_judged
    if sufficient:
        sample_line = (
            f"- 検証ペア数{len(pairs)}組・予測信号の判定{predictive_judged}件で、"
            f"サンプル目安（{min_pairs}組／{min_judged}件）を満たします。"
            "全体の率は参考指標として扱えます。各信号の率は注記が「通常」のものを優先し、"
            "信頼度更新やスコア倍率の調整に使う際は信号ごとの判定件数も確認してください。"
        )
    else:
        sample_line = (
            f"- 現時点では検証規模が小さいため（検証{len(pairs)}組／目安{min_pairs}組、"
            f"予測信号の判定{predictive_judged}件／目安{min_judged}件）、率は参考値です。"
            "信頼度更新やスコア倍率の自動調整にはまだ使わないでください。"
        )
    lines.extend(
        [
            "",
            "## 結論",
            "",
            f"- 予測信号全体は、判定{predictive_judged}件中、再現{predictive_reproduced}件、一部{predictive_partial}件です。",
            f"- 予測信号全体の再現率は{pct(predictive_reproduced, predictive_judged)}、再現+一部率は{pct(predictive_reproduced + predictive_partial, predictive_judged)}です。",
            sample_line,
            "",
            "## 予測信号",
            "",
        ]
    )
    markdown_table(lines, predictive)
    lines.extend(["", "## 結果確認", ""])
    markdown_table(lines, result_check)
    lines.extend(
        [
            "",
            "## 注意",
            "",
            "- 再現率は `再現 / 判定件数` です。判定不可は分母から除外しています。",
            "- 再現+一部率は `再現 + 一部 / 判定件数` です。",
            "- `predictive=false` の上がり3F・追い切りは、予測材料ではなく結果確認として分けています。",
            "- サンプル目安は初期値で検証20組・判定20件です。これ未満は参考値として扱います。",
        ]
    )
    return "\n".join(lines) + "\n"


def write_markdown(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8-sig")


def default_summary_paths(output_dir: Path, target_year: int | None = None) -> tuple[Path, Path, Path]:
    suffix = f"_{target_year}" if target_year else ""
    return (
        output_dir / f"trend_validation{suffix}.csv",
        output_dir / f"trend_validation_summary{suffix}.md",
        output_dir / f"trend_validation_summary{suffix}.csv",
    )


def parse_args() -> argparse.Namespace:
    config = load_config()
    parser = argparse.ArgumentParser(description="trend_validation.csv から信号別の再現率を集計します")
    parser.add_argument("--input", help="入力CSV")
    parser.add_argument("--output-md", help="出力Markdown")
    parser.add_argument("--output-csv", help="出力CSV")
    parser.add_argument("--target-year", type=int, help="対象年の予測に使うローリング過去年の集計を作成します")
    parser.add_argument("--min-pairs", type=int, default=20, help="通常評価に必要な最低検証ペア数")
    parser.add_argument("--min-judged", type=int, default=20, help="通常評価に必要な最低判定件数")
    parser.add_argument("--publish-dir", default=config["publish_dir"], help="iCloudなどへコピーする公開フォルダ")
    parser.add_argument("--no-publish", action="store_true", help="公開フォルダへコピーしない")
    parser.set_defaults(publish_to_icloud=bool(config.get("publish_to_icloud", True)))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config()
    output_dir = Path(config["output_dir"])
    default_input, default_output_md, default_output_csv = default_summary_paths(output_dir, args.target_year)
    input_path = Path(args.input) if args.input else default_input
    if not input_path.exists():
        raise FileNotFoundError(f"検証CSVが見つかりません: {input_path}")

    rows = read_validation_rows(input_path)
    summaries = summarize_rows(rows, min_pairs=args.min_pairs, min_judged=args.min_judged)
    output_md = Path(args.output_md) if args.output_md else default_output_md
    output_csv = Path(args.output_csv) if args.output_csv else default_output_csv
    write_markdown(
        output_md,
        build_markdown(rows, summaries, input_path, min_pairs=args.min_pairs, min_judged=args.min_judged),
    )
    write_summary_csv(output_csv, summaries)

    copied_paths: list[Path] = []
    if args.publish_to_icloud and not args.no_publish:
        publish_dir = Path(args.publish_dir)
        publish_dir.mkdir(parents=True, exist_ok=True)
        for path in [output_md, output_csv]:
            dst = publish_dir / path.name
            shutil.copy2(path, dst)
            copied_paths.append(dst)

    print(f"wrote {output_md}")
    print(f"wrote {output_csv}")
    for path in copied_paths:
        print(f"copied {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
