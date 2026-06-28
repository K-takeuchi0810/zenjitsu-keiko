from __future__ import annotations

import argparse
import csv
import shutil
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from collect_trends import (
    MIN_BLOODLINE_RATE_EDGE,
    MIN_BLOODLINE_STARTERS,
    MIN_BLOODLINE_TOP3,
    MIN_LATEST_COMPLETED_RACES,
    TRACK_NAMES,
    _training_accelerated,
    _training_fast_total,
    _training_good_finish,
    _training_light_finish_focus,
    _training_long_work,
    build_races,
    connect,
    display_date,
    effective_latest_min_races,
    latest_completed_dates as collect_latest_completed_dates,
    load_config,
    load_result_rows,
    pct,
    summarize,
    summarize_bloodlines,
    training_trend_notes,
    trend_sample_sufficient,
)


@dataclass
class TrendSignal:
    scope: str
    kind: str
    key: str
    label: str
    pre_value: str
    predictive: bool = True


@dataclass
class CheckResult:
    scope: str
    signal: str
    pre_value: str
    post_value: str
    verdict: str
    predictive: bool = True
    signal_type: str = "trend"


@dataclass
class BloodlineSignal:
    scope: str
    kind: str
    name: str
    pre_top3: int
    pre_starters: int
    pre_rate: float


@dataclass
class ValidationData:
    pre_races: list
    post_races: list
    trend_results: list[CheckResult]
    bloodline_results: list[CheckResult]
    training_results: list[CheckResult]


def rate(num: int, den: int) -> float:
    return num / den if den else 0.0


def count_rate(num: int, den: int, unit: str = "頭") -> str:
    return f"{num}/{den}{unit} {pct(num, den)}"


def load_races(conn: sqlite3.Connection, date_key: str):
    return build_races(load_result_rows(conn, date_key))


def completed_dates(conn: sqlite3.Connection, min_races: int, limit: int = 2) -> list[str]:
    min_races = effective_latest_min_races(min_races)
    placeholders = ",".join("?" for _ in TRACK_NAMES)
    sql = f"""
        WITH per_race AS (
            SELECT
                r.race_year || r.race_month_day AS d,
                r.track_code, r.kaiji, r.nichiji, r.race_num,
                SUM(
                    CASE
                        WHEN COALESCE(NULLIF(CAST(h.confirmed_order AS INTEGER), 0), CAST(h.finish_order AS INTEGER), 0) BETWEEN 1 AND 3
                        THEN 1 ELSE 0
                    END
                ) AS top3_count
              FROM races r
              LEFT JOIN horse_races h
                ON r.race_year = h.race_year
               AND r.race_month_day = h.race_month_day
               AND r.track_code = h.track_code
               AND r.kaiji = h.kaiji
               AND r.nichiji = h.nichiji
               AND r.race_num = h.race_num
             WHERE r.track_code IN ({placeholders})
             GROUP BY r.race_year, r.race_month_day, r.track_code, r.kaiji, r.nichiji, r.race_num
        )
        SELECT d,
               COUNT(*) AS race_count,
               SUM(CASE WHEN top3_count >= 3 THEN 1 ELSE 0 END) AS top3_races
          FROM per_race
         GROUP BY d
        HAVING race_count >= ?
           AND top3_races = race_count
         ORDER BY d DESC
         LIMIT ?
    """
    rows = conn.execute(sql, [*TRACK_NAMES.keys(), min_races, limit]).fetchall()
    return [str(row["d"]) for row in rows]


def latest_completed_pair(
    conn: sqlite3.Connection,
    min_races: int,
    expected_races_by_date: dict | None = None,
) -> tuple[str, str]:
    dates = collect_latest_completed_dates(
        conn,
        min_races,
        limit=2,
        expected_races_by_date=expected_races_by_date,
    )
    if len(dates) < 2:
        raise RuntimeError("前日検証に必要な結果確定済み開催日が2日分ありません")
    post_date, pre_date = dates[0], dates[1]
    return pre_date, post_date


def existing_validation_pairs(csv_path: Path) -> set[tuple[str, str]]:
    if not csv_path.exists():
        return set()
    pairs: set[tuple[str, str]] = set()
    with csv_path.open("r", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            pre_date = str(row.get("pre_date") or "").strip()
            post_date = str(row.get("post_date") or "").strip()
            if pre_date and post_date:
                pairs.add((pre_date, post_date))
    return pairs


def latest_validation_pair(csv_path: Path, fallback_pairs: list[tuple[str, str]] | None = None) -> tuple[str, str] | None:
    pairs = existing_validation_pairs(csv_path)
    if fallback_pairs:
        pairs.update(fallback_pairs)
    if not pairs:
        return None
    return max(pairs, key=lambda pair: (pair[1], pair[0]))


def publish_latest_validation_report(
    reports_dir: Path,
    publish_dir: Path,
    csv_path: Path,
    fallback_pairs: list[tuple[str, str]] | None = None,
) -> Path | None:
    latest_pair = latest_validation_pair(csv_path, fallback_pairs)
    if not latest_pair:
        return None
    _pre_date, post_date = latest_pair
    report_path = reports_dir / post_date / "previous_trend_check.md"
    if not report_path.exists():
        return None
    publish_dir.mkdir(parents=True, exist_ok=True)
    dst = publish_dir / report_path.name
    shutil.copy2(report_path, dst)
    return dst


def default_validation_csv_path(reports_dir: Path, target_year: int | None = None) -> Path:
    if target_year:
        return reports_dir / f"trend_validation_{target_year}.csv"
    return reports_dir / "trend_validation.csv"


def completed_adjacent_pairs(
    conn: sqlite3.Connection,
    min_races: int,
    expected_races_by_date: dict | None = None,
    *,
    limit: int = 60,
    max_gap_days: int = 1,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[tuple[str, str]]:
    dates = collect_latest_completed_dates(
        conn,
        min_races,
        limit=limit,
        expected_races_by_date=expected_races_by_date,
    )
    if start_date:
        dates = [date for date in dates if date >= start_date]
    if end_date:
        dates = [date for date in dates if date <= end_date]
    pairs: list[tuple[str, str]] = []
    for pre_date, post_date in zip(sorted(dates), sorted(dates)[1:]):
        gap = (datetime.strptime(post_date, "%Y%m%d") - datetime.strptime(pre_date, "%Y%m%d")).days
        if 0 < gap <= max_gap_days:
            pairs.append((pre_date, post_date))
    return pairs


def completed_dates_between(
    conn: sqlite3.Connection,
    min_races: int,
    start_date: str,
    end_date: str,
    expected_races_by_date: dict | None = None,
) -> list[str]:
    dates = collect_latest_completed_dates(
        conn,
        min_races,
        limit=10000,
        expected_races_by_date=expected_races_by_date,
    )
    return sorted(date for date in dates if start_date <= date <= end_date)


def rolling_history_window(target_year: int, years: int = 5) -> tuple[str, str]:
    start_year = target_year - years
    end_year = target_year - 1
    return f"{start_year}0101", f"{end_year}1231"


def rolling_history_pairs(
    conn: sqlite3.Connection,
    target_year: int,
    min_races: int,
    expected_races_by_date: dict | None = None,
    *,
    years: int = 5,
    max_gap_days: int = 1,
) -> list[tuple[str, str]]:
    start_date, end_date = rolling_history_window(target_year, years)
    dates = completed_dates_between(conn, min_races, start_date, end_date, expected_races_by_date)
    pairs: list[tuple[str, str]] = []
    for pre_date, post_date in zip(dates, dates[1:]):
        gap = (datetime.strptime(post_date, "%Y%m%d") - datetime.strptime(pre_date, "%Y%m%d")).days
        if 0 < gap <= max_gap_days:
            pairs.append((pre_date, post_date))
    return pairs


def pending_validation_pairs(
    conn: sqlite3.Connection,
    csv_path: Path,
    min_races: int,
    expected_races_by_date: dict | None = None,
    *,
    limit: int = 60,
    max_gap_days: int = 1,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[tuple[str, str]]:
    existing = existing_validation_pairs(csv_path)
    return [
        pair
        for pair in completed_adjacent_pairs(
            conn,
            min_races,
            expected_races_by_date,
            limit=limit,
            max_gap_days=max_gap_days,
            start_date=start_date,
            end_date=end_date,
        )
        if pair not in existing
    ]


def group_races(races) -> dict[tuple[str, str], list]:
    groups: dict[tuple[str, str], list] = {("ALL", "ALL"): list(races)}
    for race in races:
        groups.setdefault((race.track_code, race.surface), []).append(race)
    return groups


def scope_label(key: tuple[str, str]) -> str:
    track_code, surface = key
    if key == ("ALL", "ALL"):
        return "全体"
    return f"{TRACK_NAMES.get(track_code, track_code)} {surface}"


def signal_keys(groups: dict[tuple[str, str], list]) -> list[tuple[str, str]]:
    keys = [("ALL", "ALL")]
    keys.extend(
        sorted(
            key
            for key in groups
            if key != ("ALL", "ALL")
        )
    )
    return keys


def extract_trend_signals(stats) -> list[TrendSignal]:
    if not trend_sample_sufficient(stats):
        return []

    signals: list[TrendSignal] = []
    overall = rate(stats.top3_count, stats.starter_count)

    bucket_rates: list[tuple[str, float, int, int]] = []
    for bucket, starters in stats.starter_buckets.items():
        if bucket == "不明" or starters <= 0:
            continue
        top3 = stats.top3_buckets[bucket]
        bucket_rates.append((bucket, rate(top3, starters), top3, starters))
    if bucket_rates:
        bucket, bucket_rate, top3, starters = max(bucket_rates, key=lambda item: (item[1], item[2]))
        if top3 >= 2 and bucket_rate >= overall + 0.06:
            signals.append(
                TrendSignal(
                    stats.label,
                    "frame",
                    bucket,
                    f"{bucket}枠優勢",
                    count_rate(top3, starters),
                )
            )

    front = stats.top3_styles["逃げ"] + stats.top3_styles["先行"]
    late = stats.top3_styles["差し"] + stats.top3_styles["追込"]
    front_starters = stats.starter_styles["逃げ"] + stats.starter_styles["先行"]
    late_starters = stats.starter_styles["差し"] + stats.starter_styles["追込"]
    front_rate = rate(front, front_starters)
    late_rate = rate(late, late_starters)
    if front >= 3 and front_rate >= overall + 0.08:
        signals.append(
            TrendSignal(
                stats.label,
                "style",
                "front",
                "前目脚質優勢",
                count_rate(front, front_starters),
            )
        )
    elif late >= 3 and late_rate >= overall + 0.08:
        signals.append(
            TrendSignal(
                stats.label,
                "style",
                "late",
                "差し寄り脚質優勢",
                count_rate(late, late_starters),
            )
        )

    if stats.top3_count:
        fast_rate = rate(stats.fast3f_top3, stats.top3_count)
        if fast_rate >= 0.55:
            signals.append(
                TrendSignal(
                    stats.label,
                    "final3f",
                    "high",
                    "結果確認: 上がり3F上位多め",
                    count_rate(stats.fast3f_top3, stats.top3_count),
                    predictive=False,
                )
            )
        elif fast_rate <= 0.30:
            signals.append(
                TrendSignal(
                    stats.label,
                    "final3f",
                    "low",
                    "結果確認: 上がり3F上位少なめ",
                    count_rate(stats.fast3f_top3, stats.top3_count),
                    predictive=False,
                )
            )

    if stats.winner_popularities:
        avg_pop = sum(stats.winner_popularities) / len(stats.winner_popularities)
        if avg_pop <= 3.0:
            signals.append(
                TrendSignal(
                    stats.label,
                    "market",
                    "firm",
                    "勝ち馬人気は堅め",
                    f"平均{avg_pop:.1f}人気",
                )
            )
        elif avg_pop >= 5.0:
            signals.append(
                TrendSignal(
                    stats.label,
                    "market",
                    "longshot",
                    "人気薄の勝ち切り",
                    f"平均{avg_pop:.1f}人気",
                )
            )

    high_rate = rate(stats.high_payout_races, len(stats.races))
    if high_rate >= 0.30:
        signals.append(
            TrendSignal(
                stats.label,
                "payout",
                "high",
                "荒れ気味",
                count_rate(stats.high_payout_races, len(stats.races), "R"),
            )
        )
    return signals


def compare_signal(signal: TrendSignal, post_stats) -> CheckResult:
    if not post_stats or not trend_sample_sufficient(post_stats):
        return CheckResult(
            signal.scope,
            signal.label,
            signal.pre_value,
            "サンプル不足",
            "判定不可",
            predictive=signal.predictive,
            signal_type=signal.kind,
        )

    overall = rate(post_stats.top3_count, post_stats.starter_count)
    verdict = "未再現"
    post_value = "-"

    if signal.kind == "frame":
        starters = post_stats.starter_buckets[signal.key]
        top3 = post_stats.top3_buckets[signal.key]
        bucket_rate = rate(top3, starters)
        post_value = count_rate(top3, starters)
        if top3 >= 2 and bucket_rate >= overall + 0.06:
            verdict = "再現"
        elif bucket_rate >= overall:
            verdict = "一部"

    elif signal.kind == "style":
        if signal.key == "front":
            top3 = post_stats.top3_styles["逃げ"] + post_stats.top3_styles["先行"]
            starters = post_stats.starter_styles["逃げ"] + post_stats.starter_styles["先行"]
            other_top3 = post_stats.top3_styles["差し"] + post_stats.top3_styles["追込"]
            other_starters = post_stats.starter_styles["差し"] + post_stats.starter_styles["追込"]
        else:
            top3 = post_stats.top3_styles["差し"] + post_stats.top3_styles["追込"]
            starters = post_stats.starter_styles["差し"] + post_stats.starter_styles["追込"]
            other_top3 = post_stats.top3_styles["逃げ"] + post_stats.top3_styles["先行"]
            other_starters = post_stats.starter_styles["逃げ"] + post_stats.starter_styles["先行"]
        style_rate = rate(top3, starters)
        other_rate = rate(other_top3, other_starters)
        post_value = count_rate(top3, starters)
        if top3 >= 3 and style_rate >= overall + 0.08:
            verdict = "再現"
        elif style_rate >= overall or style_rate > other_rate:
            verdict = "一部"

    elif signal.kind == "final3f":
        fast_rate = rate(post_stats.fast3f_top3, post_stats.top3_count)
        post_value = count_rate(post_stats.fast3f_top3, post_stats.top3_count)
        if signal.key == "high":
            if fast_rate >= 0.55:
                verdict = "再現"
            elif fast_rate >= 0.50:
                verdict = "一部"
        else:
            if fast_rate <= 0.30:
                verdict = "再現"
            elif fast_rate <= 0.35:
                verdict = "一部"

    elif signal.kind == "market":
        avg_pop = sum(post_stats.winner_popularities) / len(post_stats.winner_popularities)
        post_value = f"平均{avg_pop:.1f}人気"
        if signal.key == "firm":
            if avg_pop <= 3.0:
                verdict = "再現"
            elif avg_pop <= 3.5:
                verdict = "一部"
        else:
            if avg_pop >= 5.0:
                verdict = "再現"
            elif avg_pop >= 4.5:
                verdict = "一部"

    elif signal.kind == "payout":
        high_rate = rate(post_stats.high_payout_races, len(post_stats.races))
        post_value = count_rate(post_stats.high_payout_races, len(post_stats.races), "R")
        if high_rate >= 0.30:
            verdict = "再現"
        elif high_rate >= 0.25:
            verdict = "一部"

    return CheckResult(
        signal.scope,
        signal.label,
        signal.pre_value,
        post_value,
        verdict,
        predictive=signal.predictive,
        signal_type=signal.kind,
    )


def extract_bloodline_signals(scope: str, races) -> list[BloodlineSignal]:
    stats = summarize_bloodlines(races)
    if not stats.starter_count:
        return []
    overall = rate(stats.top3_count, stats.starter_count)
    out: list[BloodlineSignal] = []

    for kind, top3_counter, starter_counter in [
        ("父", stats.sire_top3, stats.sire_starters),
        ("母父", stats.dam_sire_top3, stats.dam_sire_starters),
    ]:
        for name, top3 in top3_counter.items():
            starters = starter_counter[name]
            blood_rate = rate(top3, starters)
            if (
                starters >= MIN_BLOODLINE_STARTERS
                and top3 >= MIN_BLOODLINE_TOP3
                and blood_rate >= overall + MIN_BLOODLINE_RATE_EDGE
            ):
                out.append(BloodlineSignal(scope, kind, name, top3, starters, blood_rate))
    return sorted(out, key=lambda item: (item.pre_top3, item.pre_rate), reverse=True)


def compare_bloodline(signal: BloodlineSignal, post_races) -> CheckResult:
    stats = summarize_bloodlines(post_races)
    if not stats.starter_count:
        return CheckResult(
            signal.scope,
            f"{signal.kind} {signal.name}",
            count_rate(signal.pre_top3, signal.pre_starters),
            "出走なし",
            "判定不可",
            signal_type="bloodline",
        )

    if signal.kind == "父":
        top3 = stats.sire_top3[signal.name]
        starters = stats.sire_starters[signal.name]
    else:
        top3 = stats.dam_sire_top3[signal.name]
        starters = stats.dam_sire_starters[signal.name]

    pre_value = count_rate(signal.pre_top3, signal.pre_starters)
    if starters < MIN_BLOODLINE_STARTERS:
        return CheckResult(
            signal.scope,
            f"{signal.kind} {signal.name}",
            pre_value,
            count_rate(top3, starters),
            "判定不可",
            signal_type="bloodline",
        )

    overall = rate(stats.top3_count, stats.starter_count)
    blood_rate = rate(top3, starters)
    verdict = "未再現"
    if top3 >= MIN_BLOODLINE_TOP3 and blood_rate >= overall + MIN_BLOODLINE_RATE_EDGE:
        verdict = "再現"
    elif top3 > 0 and blood_rate >= overall:
        verdict = "一部"
    return CheckResult(
        signal.scope,
        f"{signal.kind} {signal.name}",
        pre_value,
        count_rate(top3, starters),
        verdict,
        signal_type="bloodline",
    )


def load_training_rows(conn: sqlite3.Connection, date_key: str) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    start_date = (datetime.strptime(date_key, "%Y%m%d") - timedelta(days=10)).strftime("%Y%m%d")
    sql = """
        WITH target_horses AS (
            SELECT DISTINCT
                   blood_register_num,
                   CASE
                       WHEN (
                            CAST(COALESCE(confirmed_order, 0) AS INTEGER) BETWEEN 1 AND 3
                            OR CAST(COALESCE(finish_order, 0) AS INTEGER) BETWEEN 1 AND 3
                       )
                       THEN 1 ELSE 0
                   END AS is_top3
              FROM horse_races
             WHERE race_year || race_month_day = ?
               AND TRIM(COALESCE(blood_register_num, '')) != ''
               AND TRIM(COALESCE(horse_num, '')) NOT IN ('', '00')
               AND COALESCE(NULLIF(TRIM(abnormal_code), ''), '0') = '0'
        ),
        ranked AS (
            SELECT
                t.*,
                h.is_top3,
                ROW_NUMBER() OVER (
                    PARTITION BY t.blood_register_num
                    ORDER BY t.training_date DESC, t.training_time_str DESC
                ) AS rn
              FROM training_times t
              JOIN target_horses h ON h.blood_register_num = t.blood_register_num
             WHERE t.training_date BETWEEN ? AND ?
        )
        SELECT *
          FROM ranked
         WHERE rn = 1
    """
    rows = list(conn.execute(sql, (date_key, start_date, date_key)))
    top3_rows = [row for row in rows if int(row["is_top3"] or 0) == 1]
    return rows, top3_rows


def training_metrics(conn: sqlite3.Connection, date_key: str) -> dict[str, tuple[int, int, int, int]]:
    rows, top3_rows = load_training_rows(conn, date_key)
    checks: list[tuple[str, Callable[[sqlite3.Row], bool]]] = [
        ("終い良好", _training_good_finish),
        ("加速ラップ", _training_accelerated),
        ("全体時計速い", _training_fast_total),
        ("長め負荷", _training_long_work),
        ("終い重点", _training_light_finish_focus),
    ]
    metrics: dict[str, tuple[int, int, int, int]] = {}
    for label, fn in checks:
        top_count = sum(1 for row in top3_rows if fn(row))
        base_count = sum(1 for row in rows if fn(row))
        metrics[label] = (top_count, len(top3_rows), base_count, len(rows))
    return metrics


def compare_training(conn: sqlite3.Connection, pre_date: str, post_date: str) -> list[CheckResult]:
    pre = training_metrics(conn, pre_date)
    post = training_metrics(conn, post_date)
    results: list[CheckResult] = []
    for label, (pre_top, pre_total, pre_base, pre_base_total) in pre.items():
        pre_diff = rate(pre_top, pre_total) - rate(pre_base, pre_base_total)
        if pre_top < 10 or pre_diff < 0.05:
            continue
        post_top, post_total, post_base, post_base_total = post[label]
        post_diff = rate(post_top, post_total) - rate(post_base, post_base_total)
        verdict = "未再現"
        if post_diff >= 0.05:
            verdict = "再現"
        elif post_diff >= 0:
            verdict = "一部"
        results.append(
            CheckResult(
                "最終追い切り",
                label,
                f"3着内{count_rate(pre_top, pre_total)} / 全体{count_rate(pre_base, pre_base_total)}",
                f"3着内{count_rate(post_top, post_total)} / 全体{count_rate(post_base, post_base_total)}",
                verdict,
                predictive=False,
                signal_type="training",
            )
        )
    return results


def verdict_counts(results: list[CheckResult]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for result in results:
        if not result.predictive or result.verdict == "判定不可":
            continue
        counts[result.verdict] += 1
    return counts


def write_table(lines: list[str], results: list[CheckResult], pre_date: str, post_date: str) -> None:
    lines.append(f"|範囲|{display_date(pre_date)}の傾向|{display_date(pre_date)}の数値|{display_date(post_date)}の結果|判定|")
    lines.append("|---|---|---:|---:|---|")
    for result in results:
        lines.append(
            f"|{result.scope}|{result.signal}|{result.pre_value}|{result.post_value}|{result.verdict}|"
        )


def collect_validation_data(conn: sqlite3.Connection, pre_date: str, post_date: str) -> ValidationData:
    pre_races = load_races(conn, pre_date)
    post_races = load_races(conn, post_date)
    pre_groups = group_races(pre_races)
    post_groups = group_races(post_races)

    trend_results: list[CheckResult] = []
    for key in signal_keys(pre_groups):
        pre_stats = summarize(scope_label(key), pre_groups[key])
        if not trend_sample_sufficient(pre_stats):
            continue
        post_stats = summarize(scope_label(key), post_groups.get(key, [])) if post_groups.get(key) else None
        for signal in extract_trend_signals(pre_stats):
            trend_results.append(compare_signal(signal, post_stats))

    bloodline_results: list[CheckResult] = []
    for key in signal_keys(pre_groups):
        pre_stats = summarize(scope_label(key), pre_groups[key])
        if not trend_sample_sufficient(pre_stats):
            continue
        for signal in extract_bloodline_signals(scope_label(key), pre_groups[key])[:6]:
            bloodline_results.append(compare_bloodline(signal, post_groups.get(key, [])))

    training_results = compare_training(conn, pre_date, post_date)

    return ValidationData(
        pre_races=pre_races,
        post_races=post_races,
        trend_results=trend_results,
        bloodline_results=bloodline_results,
        training_results=training_results,
    )


def all_check_results(data: ValidationData) -> list[CheckResult]:
    return data.trend_results + data.bloodline_results + data.training_results


def build_report(
    conn: sqlite3.Connection,
    db_path: Path,
    pre_date: str,
    post_date: str,
    data: ValidationData | None = None,
) -> str:
    data = data or collect_validation_data(conn, pre_date, post_date)
    pre_races = data.pre_races
    post_races = data.post_races
    trend_results = data.trend_results
    bloodline_results = data.bloodline_results
    training_results = data.training_results

    predictive_results = [result for result in trend_results + bloodline_results + training_results if result.predictive]
    counts = verdict_counts(predictive_results)
    checked = counts["再現"] + counts["一部"] + counts["未再現"]
    reproduced = counts["再現"]
    partial = counts["一部"]
    missed = counts["未再現"]

    pre_stats = summarize("全体", pre_races)
    post_stats = summarize("全体", post_races)
    pre_starters = pre_stats.starter_count
    post_starters = post_stats.starter_count

    lines: list[str] = [
        f"# 前日傾向検証レポート {display_date(pre_date)} -> {display_date(post_date)}",
        "",
        f"- 生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 元DB: `{db_path}`",
        f"- 前日: {display_date(pre_date)} {len(pre_races)}R / 通常出走馬{pre_starters}頭",
        f"- 検証日: {display_date(post_date)} {len(post_races)}R / 通常出走馬{post_starters}頭",
        "",
        "## 結論",
        "",
        f"- 予測に使える前日信号は{checked}件中、再現{reproduced}件、一部{partial}件、未再現{missed}件でした。",
        "- 脚質・枠・人気・配当・血統の内訳は下表で確認してください。特に枠と血統は条件別や出走頭数で振れやすいため、単独根拠にはしません。",
        "- 上がり3Fと最終追い切りは結果確認として別枠にしています。予測信号の件数には含めていません。",
        "- この結果は検証CSVへ保存され、週次集計レポートで信号別の再現率として蓄積されます。",
        "",
        "## 枠・脚質・人気・配当",
        "",
    ]
    write_table(lines, trend_results, pre_date, post_date)

    lines.extend(["", "## 血統", ""])
    write_table(lines, bloodline_results, pre_date, post_date)

    lines.extend(["", "## 最終追い切り", ""])
    write_table(lines, training_results, pre_date, post_date)
    lines.extend(["", f"### {display_date(pre_date)} の追い切り要約"])
    lines.extend(f"- {note}" for note in training_trend_notes(conn, pre_date)[:4])
    lines.extend(["", f"### {display_date(post_date)} の追い切り要約"])
    lines.extend(f"- {note}" for note in training_trend_notes(conn, post_date)[:4])

    lines.extend(
        [
            "",
            "## 注意",
            "",
            "- 上がり3Fはレース後に判明する結果確認項目です。前日から翌日の予測信号としては数えていません。",
            "- 取消・除外などの異常投票行は、出走頭数と率の分母から除外しています。",
            "- これは1日分の前日傾向検証であり、回収率や長期的な有効性を示すバックテストではありません。",
        ]
    )
    return "\n".join(lines) + "\n"


VALIDATION_CSV_FIELDS = [
    "generated_at",
    "pre_date",
    "post_date",
    "scope",
    "signal_type",
    "signal",
    "predictive",
    "pre_value",
    "post_value",
    "verdict",
]


def validation_csv_rows(
    pre_date: str,
    post_date: str,
    results: list[CheckResult],
    generated_at: str | None = None,
) -> list[dict[str, str]]:
    generated_at = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return [
        {
            "generated_at": generated_at,
            "pre_date": pre_date,
            "post_date": post_date,
            "scope": result.scope,
            "signal_type": result.signal_type,
            "signal": result.signal,
            "predictive": "true" if result.predictive else "false",
            "pre_value": result.pre_value,
            "post_value": result.post_value,
            "verdict": result.verdict,
        }
        for result in results
    ]


def write_validation_csv(
    csv_path: Path,
    pre_date: str,
    post_date: str,
    results: list[CheckResult],
) -> int:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    existing_rows: list[dict[str, str]] = []
    if csv_path.exists():
        with csv_path.open("r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("pre_date") == pre_date and row.get("post_date") == post_date:
                    continue
                existing_rows.append({field: row.get(field, "") for field in VALIDATION_CSV_FIELDS})

    new_rows = validation_csv_rows(pre_date, post_date, results)
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=VALIDATION_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(existing_rows)
        writer.writerows(new_rows)
    return len(new_rows)


def run_validation_pair(
    conn: sqlite3.Connection,
    *,
    db_path: Path,
    reports_dir: Path,
    pre_date: str,
    post_date: str,
    output_path: Path | None,
    validation_csv_path: Path,
    write_csv: bool,
    publish_dir: Path,
    publish: bool,
) -> tuple[Path, int, list[Path]]:
    report_path = output_path or reports_dir / post_date / "previous_trend_check.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    data = collect_validation_data(conn, pre_date, post_date)
    report = build_report(conn, db_path, pre_date, post_date, data)
    report_path.write_text(report, encoding="utf-8-sig")

    validation_row_count = 0
    if write_csv:
        validation_row_count = write_validation_csv(
            validation_csv_path,
            pre_date,
            post_date,
            all_check_results(data),
        )

    copied_paths: list[Path] = []
    if publish:
        history_dir = publish_dir / "history" / post_date
        history_dir.mkdir(parents=True, exist_ok=True)
        for publish_path in [history_dir / report_path.name, publish_dir / report_path.name]:
            shutil.copy2(report_path, publish_path)
            copied_paths.append(publish_path)
        if write_csv and validation_csv_path.exists():
            publish_csv_path = publish_dir / validation_csv_path.name
            shutil.copy2(validation_csv_path, publish_csv_path)
            copied_paths.append(publish_csv_path)
    return report_path, validation_row_count, copied_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="前日傾向が翌日に表れたかを検証するレポートを生成します")
    parser.add_argument("--pre-date", help="前日 YYYYMMDD")
    parser.add_argument("--post-date", help="検証日 YYYYMMDD")
    parser.add_argument("--latest-pair", action="store_true", help="結果確定済みの最新2開催日を自動選択")
    parser.add_argument("--pending-pairs", action="store_true", help="検証CSVに未登録の隣接開催ペアをすべて処理")
    parser.add_argument("--pair-lookback", type=int, default=60, help="未処理ペア探索で見る結果確定済み開催日の最大数")
    parser.add_argument("--max-pair-gap-days", type=int, default=1, help="前日検証ペアとして扱う開催日間隔の最大日数")
    parser.add_argument("--target-year", type=int, help="この年の予測に使う過去年。例: 2026なら2021-2025")
    parser.add_argument("--history-years", type=int, default=5, help="--target-year 指定時に使う過去年数")
    parser.add_argument(
        "--min-races",
        type=int,
        default=MIN_LATEST_COMPLETED_RACES,
        help=f"最新2開催日の判定に必要な最低レース数（下限{MIN_LATEST_COMPLETED_RACES}R）",
    )
    parser.add_argument("--output", help="出力先。未指定なら reports/<post-date>/previous_trend_check.md")
    parser.add_argument("--validation-csv", help="検証結果CSV。未指定なら reports/trend_validation.csv")
    parser.add_argument("--no-validation-csv", action="store_true", help="検証結果CSVへ保存しない")
    parser.add_argument("--no-publish", action="store_true", help="iCloud publish_dir へコピーしない")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config()
    db_path = Path(config["source_db"])
    reports_dir = Path(config["output_dir"])
    validation_csv_path = Path(args.validation_csv) if args.validation_csv else default_validation_csv_path(
        reports_dir,
        args.target_year,
    )
    expected_races_by_date = config.get("expected_races_by_date", {})
    publish_dir = Path(config["publish_dir"])
    should_publish = bool(config.get("publish_to_icloud", True)) and not args.no_publish
    if args.pending_pairs and args.output:
        raise ValueError("--pending-pairs では --output を指定できません")
    if args.target_year and not args.pending_pairs:
        raise ValueError("--target-year は --pending-pairs と組み合わせて指定してください")

    with connect(db_path) as conn:
        if args.pending_pairs:
            if args.target_year:
                window_start, window_end = rolling_history_window(args.target_year, args.history_years)
                existing = existing_validation_pairs(validation_csv_path)
                pairs = [
                    pair
                    for pair in rolling_history_pairs(
                        conn,
                        args.target_year,
                        args.min_races,
                        expected_races_by_date,
                        years=args.history_years,
                        max_gap_days=args.max_pair_gap_days,
                    )
                    if pair not in existing
                ]
                print(
                    f"history window for {args.target_year}: "
                    f"{display_date(window_start)}..{display_date(window_end)}"
                )
            else:
                pairs = pending_validation_pairs(
                    conn,
                    validation_csv_path,
                    args.min_races,
                    expected_races_by_date,
                    limit=args.pair_lookback,
                    max_gap_days=args.max_pair_gap_days,
                )
            if not pairs:
                print(f"pending validation pairs: 0 ({validation_csv_path})")
                if should_publish:
                    latest_dst = publish_latest_validation_report(reports_dir, publish_dir, validation_csv_path)
                    if latest_dst:
                        print(f"copied {latest_dst}")
                return 0
            print(f"pending validation pairs: {len(pairs)}")
        elif args.latest_pair:
            pairs = [latest_completed_pair(conn, args.min_races, expected_races_by_date)]
        else:
            if not args.pre_date or not args.post_date:
                raise ValueError("--pending-pairs / --latest-pair を使うか、--pre-date と --post-date を両方指定してください")
            pairs = [(args.pre_date, args.post_date)]

        for pre_date, post_date in pairs:
            report_path, validation_row_count, copied_paths = run_validation_pair(
                conn,
                db_path=db_path,
                reports_dir=reports_dir,
                pre_date=pre_date,
                post_date=post_date,
                output_path=Path(args.output) if args.output else None,
                validation_csv_path=validation_csv_path,
                write_csv=not args.no_validation_csv,
                publish_dir=publish_dir,
                publish=should_publish,
            )
            print(f"wrote {report_path}")
            if not args.no_validation_csv:
                print(f"updated {validation_csv_path} ({validation_row_count} rows for {pre_date}->{post_date})")
            for path in copied_paths:
                print(f"copied {path}")
        if args.pending_pairs and should_publish:
            latest_dst = publish_latest_validation_report(reports_dir, publish_dir, validation_csv_path, pairs)
            if latest_dst:
                print(f"copied {latest_dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
