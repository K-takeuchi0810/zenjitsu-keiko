from __future__ import annotations

import argparse
import csv
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from collect_trends import (
    RECOMMENDATION_LOG_FILE,
    build_races,
    connect,
    date_status,
    display_date,
    load_config,
    load_result_rows,
    pct,
    read_recommendation_log,
    update_recommendation_results,
)


SUMMARY_FIELDS = [
    "group",
    "label",
    "total",
    "evaluated",
    "bet_count",
    "wins",
    "top3",
    "missing",
    "win_rate",
    "top3_rate",
    "win_return",
    "place_return",
    "win_return_rate",
    "place_return_rate",
]


@dataclass
class RecommendationSummary:
    group: str
    label: str
    total: int
    evaluated: int
    bet_count: int
    wins: int
    top3: int
    missing: int
    win_return: int
    place_return: int

    @property
    def win_rate(self) -> str:
        return pct(self.wins, self.evaluated)

    @property
    def top3_rate(self) -> str:
        return pct(self.top3, self.evaluated)

    @property
    def win_return_rate(self) -> str:
        return pct(self.win_return, self.bet_count * 100)

    @property
    def place_return_rate(self) -> str:
        return pct(self.place_return, self.bet_count * 100)


def parse_bool(value: str) -> bool:
    return str(value or "").strip().lower() == "true"


def parse_int(value: str) -> int:
    try:
        return int(str(value or "0").strip() or 0)
    except ValueError:
        return 0


def is_evaluated(row: dict[str, str]) -> bool:
    return bool(row.get("result_status"))


def score_band(row: dict[str, str]) -> str:
    try:
        score = int(row.get("score") or 0)
    except ValueError:
        score = 0
    if score >= 90:
        return "90点以上"
    if score >= 80:
        return "80-89点"
    if score >= 70:
        return "70-79点"
    if score >= 62:
        return "62-69点"
    return "62点未満"


def summarize_group(rows: list[dict[str, str]], group: str, label: str) -> RecommendationSummary:
    evaluated_rows = [row for row in rows if is_evaluated(row)]
    bet_rows = [row for row in evaluated_rows if row.get("result_status") != "対象馬なし"]
    return RecommendationSummary(
        group=group,
        label=label,
        total=len(rows),
        evaluated=len(evaluated_rows),
        bet_count=len(bet_rows),
        wins=sum(1 for row in evaluated_rows if parse_bool(row.get("win", ""))),
        top3=sum(1 for row in evaluated_rows if parse_bool(row.get("in_top3", ""))),
        missing=sum(1 for row in evaluated_rows if row.get("result_status") == "対象馬なし"),
        win_return=sum(parse_int(row.get("win_return", "")) for row in bet_rows),
        place_return=sum(parse_int(row.get("place_return", "")) for row in bet_rows),
    )


# 高スコア帯（本来は勝ちやすいはずの帯）が不振な場合に警告を出すための設定。
HIGH_BAND_LABELS = ("90点以上", "80-89点")
# この件数以上たまってから監視対象にする（少数の不運を騒がないため）。
HIGH_BAND_MIN_SAMPLE = 10
# 複勝回収率がこの値（%）を下回ったら注意喚起する。
HIGH_BAND_PLACE_RETURN_FLOOR = 80


def high_band_alerts(summaries: list[RecommendationSummary]) -> list[str]:
    alerts: list[str] = []
    for row in summaries:
        if row.group != "スコア帯" or row.label not in HIGH_BAND_LABELS:
            continue
        if row.bet_count < HIGH_BAND_MIN_SAMPLE:
            continue
        place_rate = round(row.place_return / (row.bet_count * 100) * 100) if row.bet_count else 0
        if row.wins == 0:
            alerts.append(
                f"{row.label}: 馬券{row.bet_count}件で勝ち0（複勝率{row.top3_rate}・複勝回収{row.place_return_rate}）。"
                "高スコア帯が未勝利のため、スコア妥当性の確認対象として監視してください。"
            )
        elif place_rate < HIGH_BAND_PLACE_RETURN_FLOOR:
            alerts.append(
                f"{row.label}: 馬券{row.bet_count}件で複勝回収{row.place_return_rate}（{HIGH_BAND_PLACE_RETURN_FLOOR}%割れ）。"
                "高スコア帯の複勝回収が低水準のため監視してください。"
            )
    return alerts


def summarize_rows(rows: list[dict[str, str]]) -> list[RecommendationSummary]:
    summaries: list[RecommendationSummary] = [summarize_group(rows, "全体", "全推奨")]
    for band in ["90点以上", "80-89点", "70-79点", "62-69点", "62点未満"]:
        band_rows = [row for row in rows if score_band(row) == band]
        if band_rows:
            summaries.append(summarize_group(band_rows, "スコア帯", band))
    sources = sorted({row.get("trend_source", "") for row in rows if row.get("trend_source", "")})
    for source in sources:
        source_rows = [row for row in rows if row.get("trend_source", "") == source]
        summaries.append(summarize_group(source_rows, "傾向元", source))
    return summaries


def summary_csv_rows(summaries: list[RecommendationSummary]) -> list[dict[str, str]]:
    return [
        {
            "group": row.group,
            "label": row.label,
            "total": str(row.total),
            "evaluated": str(row.evaluated),
            "bet_count": str(row.bet_count),
            "wins": str(row.wins),
            "top3": str(row.top3),
            "missing": str(row.missing),
            "win_rate": row.win_rate,
            "top3_rate": row.top3_rate,
            "win_return": str(row.win_return),
            "place_return": str(row.place_return),
            "win_return_rate": row.win_return_rate,
            "place_return_rate": row.place_return_rate,
        }
        for row in summaries
    ]


def write_summary_csv(path: Path, summaries: list[RecommendationSummary]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(summary_csv_rows(summaries))


def markdown_table(lines: list[str], summaries: list[RecommendationSummary]) -> None:
    lines.append("|区分|対象|推奨数|評価済み|馬券対象|勝ち|3着内|対象馬なし|勝率|3着内率|単回収|複回収|")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for row in summaries:
        lines.append(
            f"|{row.group}|{row.label}|{row.total}|{row.evaluated}|{row.bet_count}|{row.wins}|{row.top3}|"
            f"{row.missing}|{row.win_rate}|{row.top3_rate}|{row.win_return_rate}|{row.place_return_rate}|"
        )


def build_markdown(rows: list[dict[str, str]], summaries: list[RecommendationSummary], input_path: Path) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    target_dates = sorted({row.get("target_date", "") for row in rows if row.get("target_date", "")})
    target_text = ", ".join(display_date(date) for date in target_dates[:8])
    if len(target_dates) > 8:
        target_text += f" ほか{len(target_dates) - 8}日"

    overall = summaries[0] if summaries else summarize_group([], "全体", "全推奨")
    lines = [
        "# おすすめ馬 結果検証レポート",
        "",
        f"- 生成日時: {generated_at}",
        f"- 入力CSV: `{input_path}`",
        f"- 推奨数: {overall.total}",
        f"- 評価済み: {overall.evaluated}",
        f"- 勝率: {overall.win_rate}",
        f"- 3着内率: {overall.top3_rate}",
        f"- 単勝回収率: {overall.win_return_rate}",
        f"- 複勝回収率: {overall.place_return_rate}",
    ]
    if target_text:
        lines.append(f"- 対象日: {target_text}")
    alerts = high_band_alerts(summaries)
    if alerts:
        lines.extend(["", "## 監視", ""])
        lines.extend(f"- ⚠️ {alert}" for alert in alerts)
    lines.extend(
        [
            "",
            "## 集計",
            "",
        ]
    )
    markdown_table(lines, summaries)
    lines.extend(
        [
            "",
            "## 注意",
            "",
            "- 回収率は、各推奨を単勝100円・複勝100円で買った場合の単純集計です。",
            "- `対象馬なし` は馬券対象数から除外しています。",
            "- ワイド・馬連・三連複などの買い目回収率は未集計です。",
            "- `対象馬なし` は、取消・除外などで結果側の通常出走馬に見つからなかった推奨です。",
            "- サンプルが少ない間は率を参考値として扱ってください。",
        ]
    )
    return "\n".join(lines) + "\n"


def update_pending_results(log_path: Path, db_path: Path, *, all_rows: bool = False) -> int:
    rows = read_recommendation_log(log_path)
    if not rows:
        return 0
    target_dates = sorted(
        {
            row.get("target_date", "")
            for row in rows
            if row.get("target_date", "") and (all_rows or not is_evaluated(row))
        }
    )
    updated = 0
    with connect(db_path) as conn:
        for target_date in target_dates:
            status = date_status(conn, target_date)
            if status["races"] <= 0 or status["top3_races"] < status["races"]:
                continue
            races = build_races(load_result_rows(conn, target_date))
            updated += update_recommendation_results(log_path, target_date, races)
    return updated


def parse_args() -> argparse.Namespace:
    config = load_config()
    output_dir = Path(config["output_dir"])
    parser = argparse.ArgumentParser(description="おすすめ馬ログへ実結果を反映し、成績を集計します")
    parser.add_argument("--log", default=str(output_dir / RECOMMENDATION_LOG_FILE), help="おすすめ馬ログCSV")
    parser.add_argument("--db", default=config["source_db"], help="keiba.db のパス")
    parser.add_argument("--output-md", default=str(output_dir / "recommendation_validation_summary.md"), help="出力Markdown")
    parser.add_argument("--output-csv", default=str(output_dir / "recommendation_validation_summary.csv"), help="出力CSV")
    parser.add_argument("--all", action="store_true", help="評価済み行も再評価する")
    parser.add_argument("--publish-dir", default=config["publish_dir"], help="iCloudなどへコピーする公開フォルダ")
    parser.add_argument("--no-publish", action="store_true", help="公開フォルダへコピーしない")
    parser.set_defaults(publish_to_icloud=bool(config.get("publish_to_icloud", True)))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    log_path = Path(args.log)
    db_path = Path(args.db)
    updated = update_pending_results(log_path, db_path, all_rows=args.all)
    rows = read_recommendation_log(log_path)
    summaries = summarize_rows(rows)
    output_md = Path(args.output_md)
    output_csv = Path(args.output_csv)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(build_markdown(rows, summaries, log_path), encoding="utf-8-sig")
    write_summary_csv(output_csv, summaries)
    print(f"updated recommendation results: {updated}")
    print(f"wrote {output_md}")
    print(f"wrote {output_csv}")

    if bool(getattr(args, "publish_to_icloud", True)) and not args.no_publish:
        publish_dir = Path(args.publish_dir)
        publish_dir.mkdir(parents=True, exist_ok=True)
        for path in [log_path, output_md, output_csv]:
            if path.exists():
                dst = publish_dir / path.name
                shutil.copy2(path, dst)
                print(f"copied {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
