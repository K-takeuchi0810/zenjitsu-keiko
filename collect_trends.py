from __future__ import annotations

import argparse
import csv
import html
import json
import re
import shutil
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
REPORT_FILE_MAP = {
    "mobile": "mobile.html",
    "trend": "trend.md",
    "races": "races.csv",
}
RECOMMENDATION_LOG_FILE = "recommendation_log.csv"
RECOMMENDATION_MIN_SCORE = 62
REFERENCE_CANDIDATE_MIN_SCORE = 58
MIN_TREND_RACES = 3
MIN_TREND_STARTERS = 24
MIN_LATEST_COMPLETED_RACES = 24
MIN_BLOODLINE_STARTERS = 5
MIN_BLOODLINE_TOP3 = 2
MIN_BLOODLINE_RATE_EDGE = 0.10
STANDARD_RACES_PER_TRACK = 12
SCORE_CONFIDENCE_FACTORS = {
    "中": 1.0,
    "低": 0.35,
    "対象外": 0.0,
}
INDIVIDUAL_TRAINING_SCORE_FACTOR = 0.40
MIN_INTRADAY_WEIGHT_RACES = 6
MIN_INTRADAY_SIGNAL_STARTERS = 30

TRACK_NAMES = {
    "01": "札幌",
    "02": "函館",
    "03": "福島",
    "04": "新潟",
    "05": "東京",
    "06": "中山",
    "07": "中京",
    "08": "京都",
    "09": "阪神",
    "10": "小倉",
}

WEATHER_NAMES = {
    "0": "",
    "1": "晴",
    "2": "曇",
    "3": "小雨",
    "4": "雨",
    "5": "小雪",
    "6": "雪",
}

GROUND_NAMES = {
    "0": "",
    "1": "良",
    "2": "稍重",
    "3": "重",
    "4": "不良",
}

STYLE_NAMES = {
    "1": "逃げ",
    "2": "先行",
    "3": "差し",
    "4": "追込",
}

FRAME_BUCKETS = {
    "内": {1, 2, 3},
    "中": {4, 5, 6},
    "外": {7, 8},
}


@dataclass
class HorseResult:
    horse_num: str
    frame: int
    name: str
    order: int
    popularity: int
    final3f: int
    style: str
    sire_name: str = ""
    dam_sire_name: str = ""
    final3f_rank: int = 0
    mining_rank: int = 0
    burden_weight: int = 0
    horse_weight: int = 0
    weight_change: int | None = None


@dataclass
class RaceResult:
    date: str
    track_code: str
    race_num: int
    race_name: str
    surface: str
    distance: int
    band: str
    weather: str
    ground: str
    horses: list[HorseResult]
    tan_payout: int = 0
    umaren_payout: int = 0
    sanrenpuku_payout: int = 0
    tan_payouts: dict[str, int] = field(default_factory=dict)
    fuku_payouts: dict[str, int] = field(default_factory=dict)

    @property
    def track(self) -> str:
        return TRACK_NAMES.get(self.track_code, self.track_code)

    @property
    def winner(self) -> HorseResult | None:
        return self.ordered[:1][0] if self.ordered else None

    @property
    def top3(self) -> list[HorseResult]:
        return self.ordered[:3]

    @property
    def ordered(self) -> list[HorseResult]:
        return sorted([h for h in self.horses if h.order > 0], key=lambda h: h.order)

    @property
    def condition(self) -> str:
        parts = [self.surface]
        if self.ground:
            parts.append(self.ground)
        if self.distance:
            parts.append(f"{self.distance}m")
        return "".join(parts)


@dataclass
class TrendStats:
    label: str
    races: list[RaceResult]
    starter_count: int
    starter_frames: Counter
    starter_buckets: Counter
    starter_styles: Counter
    winner_frames: Counter
    top3_frames: Counter
    winner_buckets: Counter
    top3_buckets: Counter
    winner_styles: Counter
    top3_styles: Counter
    winner_popularities: list[int]
    top3_popularities: list[int]
    fast3f_top3: int
    top3_count: int
    high_payout_races: int


@dataclass
class BloodlineStats:
    sire_starters: Counter
    sire_top3: Counter
    sire_wins: Counter
    dam_sire_starters: Counter
    dam_sire_top3: Counter
    dam_sire_wins: Counter
    starter_count: int
    top3_count: int
    race_count: int


@dataclass
class JockeyCourseStats:
    starts: int = 0
    wins: int = 0
    top3: int = 0

    @property
    def win_rate(self) -> float:
        return self.wins / self.starts if self.starts else 0.0

    @property
    def top3_rate(self) -> float:
        return self.top3 / self.starts if self.starts else 0.0


@dataclass
class NextHorse:
    track_code: str
    race_num: int
    race_name: str
    surface: str
    distance: int
    band: str
    start_time: str
    horse_num: str
    blood_register_num: str
    frame: int
    name: str
    jockey_code: str
    jockey: str
    odds: float
    popularity: int
    style: str
    mining_rank: int
    sire_name: str = ""
    dam_sire_name: str = ""
    jockey_course: JockeyCourseStats | None = None
    training: dict | None = None

    @property
    def track(self) -> str:
        return TRACK_NAMES.get(self.track_code, self.track_code)

    @property
    def condition(self) -> str:
        return f"{self.surface}{self.distance}m" if self.distance else self.surface


@dataclass
class Recommendation:
    horse: NextHorse
    score: int
    trend_source: str
    reasons: list[str]


@dataclass
class TrendWeights:
    frame: float = 1.0
    style: float = 1.0
    market: float = 1.0
    payout: float = 1.0
    mining: float = 1.0
    training: float = 1.0
    bloodline: float = 1.0
    jockey: float = 1.0
    notes: list[str] = field(default_factory=list)
    applied_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class NextPickDataStatus:
    next_date: str | None
    race_count: int
    horse_count: int
    training_count: int
    mining_count: int
    recommendation_count: int
    recommendation_race_count: int
    zero_reason: str | None
    warnings: list[str]


@dataclass
class NextRaceSummary:
    track_code: str
    race_num: int
    track: str
    race_name: str
    start_time: str
    condition: str
    race_id: str
    recommendations: list[Recommendation]
    reference_recommendations: list[Recommendation] = field(default_factory=list)


def load_config() -> dict:
    default = {
        "source_db": r"C:\Users\kizun\dev\keiba-yosou\data\keiba.db",
        "output_dir": str(APP_DIR / "reports"),
        "publish_dir": str(Path.home() / "iCloudDrive" / "傾向収集"),
        "publish_to_icloud": True,
        "publish_to_docs": True,
        "docs_dir": str(APP_DIR / "docs"),
        "latest_min_races": MIN_LATEST_COMPLETED_RACES,
        "expected_races_by_date": {},
    }
    if not CONFIG_PATH.exists():
        return default
    loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {**default, **loaded}


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"DBが見つかりません: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_date(value: str) -> str:
    if value.lower() == "today":
        return datetime.now().strftime("%Y%m%d")
    value = value.replace("-", "").replace("/", "")
    if len(value) != 8 or not value.isdigit():
        raise ValueError("日付は YYYYMMDD / YYYY-MM-DD / today の形式で指定してください")
    return value


def display_date(date_key: str) -> str:
    return f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:]}"


def time_hhmm(value: str) -> str:
    if len(value) == 4 and value.isdigit():
        return f"{value[:2]}:{value[2:]}"
    return value


def result_order(row: sqlite3.Row) -> int:
    confirmed = int(row["confirmed_order"] or 0)
    finish = int(row["finish_order"] or 0)
    return confirmed if confirmed > 0 else finish


def is_normal_starter_row(row: sqlite3.Row) -> bool:
    horse_num = str(row["horse_num"] or "").strip()
    if horse_num in {"", "00"}:
        return False
    abnormal = str(row["abnormal_code"] or "").strip()
    return abnormal in {"", "0"}


def normalize_horse_num(value: object) -> str:
    text = str(value or "").strip()
    if text in {"", "00"}:
        return ""
    return text.lstrip("0") or "0"


def surface_from_code(code: str) -> str:
    try:
        value = int((code or "").strip())
    except ValueError:
        return code or "不明"
    if 10 <= value <= 22:
        return "芝"
    if 23 <= value <= 29:
        return "ダート"
    if 51 <= value <= 59:
        return "障害"
    return code or "不明"


def distance_band(surface: str, distance: int) -> str:
    if surface == "障害":
        return "障害"
    if distance <= 1400:
        return "短距離"
    if distance <= 1600:
        return "マイル"
    if distance <= 2200:
        return "中距離"
    return "長距離"


def frame_bucket(frame: int) -> str:
    for name, frames in FRAME_BUCKETS.items():
        if frame in frames:
            return name
    return "不明"


def pct(num: int, den: int) -> str:
    if den <= 0:
        return "-"
    return f"{num / den * 100:.0f}%"


def fmt_money(value: int) -> str:
    return f"{value:,}" if value else "-"


def race_label(track: str, race_num: int, race_name: str) -> str:
    name = (race_name or "").strip()
    base = f"{track} {race_num}R"
    if not name or name == f"{race_num}R":
        return base
    return f"{base} {name}"


def effective_latest_min_races(value: int) -> int:
    return max(value, MIN_LATEST_COMPLETED_RACES)


def configured_expected_races(expected_races_by_date: dict | None, date_key: str) -> int:
    if not expected_races_by_date:
        return 0
    value = expected_races_by_date.get(date_key) or expected_races_by_date.get(display_date(date_key))
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def race_profile(conn: sqlite3.Connection, date_key: str) -> dict[str, int]:
    placeholders = ",".join("?" for _ in TRACK_NAMES)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS race_count,
               COUNT(DISTINCT track_code) AS track_count
          FROM races
         WHERE race_year || race_month_day = ?
           AND track_code IN ({placeholders})
        """,
        [date_key, *TRACK_NAMES.keys()],
    ).fetchone()
    return {
        "race_count": int(row["race_count"] or 0),
        "track_count": int(row["track_count"] or 0),
    }


def neighbor_date_keys(date_key: str) -> list[str]:
    current = datetime.strptime(date_key, "%Y%m%d")
    return [
        (current - timedelta(days=1)).strftime("%Y%m%d"),
        (current + timedelta(days=1)).strftime("%Y%m%d"),
    ]


def expected_races_for_date(
    conn: sqlite3.Connection,
    date_key: str,
    min_races: int,
    expected_races_by_date: dict | None = None,
) -> int:
    min_races = effective_latest_min_races(min_races)
    configured = configured_expected_races(expected_races_by_date, date_key)
    own_profile = race_profile(conn, date_key)
    max_track_count = own_profile["track_count"]
    for neighbor in neighbor_date_keys(date_key):
        max_track_count = max(max_track_count, race_profile(conn, neighbor)["track_count"])
    visible_expected = max_track_count * STANDARD_RACES_PER_TRACK if max_track_count else 0
    return max(min_races, configured, visible_expected)


def completed_date_candidates(conn: sqlite3.Connection, min_races: int, limit: int = 30) -> list[dict[str, int | str]]:
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
    return [
        {
            "date": str(row["d"]),
            "race_count": int(row["race_count"] or 0),
            "top3_races": int(row["top3_races"] or 0),
        }
        for row in rows
    ]


def latest_completed_dates(
    conn: sqlite3.Connection,
    min_races: int,
    limit: int = 1,
    expected_races_by_date: dict | None = None,
) -> list[str]:
    dates: list[str] = []
    for candidate in completed_date_candidates(conn, min_races, limit=max(30, limit * 5)):
        date_key = str(candidate["date"])
        race_count = int(candidate["race_count"])
        expected = expected_races_for_date(conn, date_key, min_races, expected_races_by_date)
        if race_count < expected:
            continue
        dates.append(date_key)
        if len(dates) >= limit:
            break
    if not dates:
        raise RuntimeError("結果確定済みの中央競馬データが見つかりません")
    return dates


def latest_completed_date(
    conn: sqlite3.Connection,
    min_races: int,
    expected_races_by_date: dict | None = None,
) -> str:
    return latest_completed_dates(conn, min_races, limit=1, expected_races_by_date=expected_races_by_date)[0]


def latest_race_date(conn: sqlite3.Connection) -> str | None:
    placeholders = ",".join("?" for _ in TRACK_NAMES)
    row = conn.execute(
        f"""
        SELECT MAX(race_year || race_month_day) AS d
          FROM races
         WHERE track_code IN ({placeholders})
        """,
        [*TRACK_NAMES.keys()],
    ).fetchone()
    return str(row["d"]) if row and row["d"] else None


def date_status(conn: sqlite3.Connection, date_key: str) -> dict[str, int]:
    placeholders = ",".join("?" for _ in TRACK_NAMES)
    row = conn.execute(
        f"""
        WITH per_race AS (
            SELECT
                r.track_code, r.kaiji, r.nichiji, r.race_num,
                COUNT(
                    CASE
                        WHEN TRIM(COALESCE(h.horse_num, '')) NOT IN ('', '00')
                        THEN 1
                    END
                ) AS horses,
                SUM(
                    CASE
                        WHEN CAST(COALESCE(h.confirmed_order, 0) AS INTEGER) > 0
                          OR CAST(COALESCE(h.finish_order, 0) AS INTEGER) > 0
                        THEN 1 ELSE 0
                    END
                ) AS finished,
                SUM(
                    CASE
                        WHEN COALESCE(NULLIF(CAST(h.confirmed_order AS INTEGER), 0), CAST(h.finish_order AS INTEGER), 0) BETWEEN 1 AND 3
                        THEN 1 ELSE 0
                    END
                ) AS top3_count,
                MAX(
                    CASE
                        WHEN COALESCE(p.tan_payout1, 0) > 0
                         AND COALESCE(p.umaren_payout1, 0) > 0
                         AND COALESCE(p.sanrenpuku_payout1, 0) > 0
                        THEN 1 ELSE 0
                    END
                ) AS has_payout
              FROM races r
              LEFT JOIN horse_races h
                ON r.race_year = h.race_year
               AND r.race_month_day = h.race_month_day
               AND r.track_code = h.track_code
               AND r.kaiji = h.kaiji
               AND r.nichiji = h.nichiji
               AND r.race_num = h.race_num
              LEFT JOIN payouts p
                ON r.race_year = p.race_year
               AND r.race_month_day = p.race_month_day
               AND r.track_code = p.track_code
               AND r.kaiji = p.kaiji
               AND r.nichiji = p.nichiji
               AND r.race_num = p.race_num
             WHERE r.race_year || r.race_month_day = ?
               AND r.track_code IN ({placeholders})
             GROUP BY r.track_code, r.kaiji, r.nichiji, r.race_num
        )
        SELECT COUNT(*) AS races,
               COALESCE(SUM(horses), 0) AS horses,
               COALESCE(SUM(finished), 0) AS finished,
               COALESCE(SUM(CASE WHEN top3_count >= 3 THEN 1 ELSE 0 END), 0) AS top3_races,
               COALESCE(SUM(has_payout), 0) AS payout_races,
               COALESCE(SUM(CASE WHEN top3_count >= 3 THEN 1 ELSE 0 END), 0) AS complete_races
          FROM per_race
        """,
        [date_key, *TRACK_NAMES.keys()],
    ).fetchone()
    return {
        "races": int(row["races"] or 0),
        "horses": int(row["horses"] or 0),
        "finished": int(row["finished"] or 0),
        "top3_races": int(row["top3_races"] or 0),
        "payout_races": int(row["payout_races"] or 0),
        "complete_races": int(row["complete_races"] or 0),
    }


def no_result_message(date_key: str, status: dict[str, int]) -> str:
    if status["races"] > 0 and status["finished"] == 0:
        return (
            f"{display_date(date_key)} は出馬表 {status['races']}R / {status['horses']}頭 がDBにありますが、"
            "着順・払戻がまだ入っていません。JRA-VANの結果データ配信後に再同期してください。"
        )
    if status["races"] > 0 and status["complete_races"] < status["races"]:
        return (
            f"{display_date(date_key)} は結果が不完全です。"
            f"3着内確定 {status['top3_races']}/{status['races']}R、"
            f"払戻あり {status['payout_races']}/{status['races']}R。"
            "全レースの3着内が揃ってから集計してください。"
        )
    return (
        f"{display_date(date_key)} の結果確定データがありません。"
        "JRA-VANのRACEデータ取得とDB取り込み後に再実行してください。"
    )


def payout_warning(date_key: str, status: dict[str, int]) -> str | None:
    if status["races"] > 0 and status["top3_races"] == status["races"] and status["payout_races"] < status["races"]:
        return (
            f"{display_date(date_key)} は全レースの3着内は揃っていますが、"
            f"主要払戻が {status['payout_races']}/{status['races']}R しかありません。"
            "配当・荒れ度の集計は欠損の影響を受けます。"
        )
    return None


def load_result_rows(conn: sqlite3.Connection, date_key: str) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in TRACK_NAMES)
    payout_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(payouts)").fetchall()}
    horse_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(horse_races)").fetchall()}

    def payout_column(name: str) -> str:
        if name in payout_columns:
            return f"p.{name}"
        return f"0 AS {name}"

    def horse_column(name: str, default: str = "0") -> str:
        if name in horse_columns:
            return f"h.{name}"
        return f"{default} AS {name}"

    payout_fields = [
        "tan_horse_num1",
        "tan_payout1",
        "tan_horse_num2",
        "tan_payout2",
        "tan_horse_num3",
        "tan_payout3",
        "fuku_horse_num1",
        "fuku_payout1",
        "fuku_horse_num2",
        "fuku_payout2",
        "fuku_horse_num3",
        "fuku_payout3",
        "fuku_horse_num4",
        "fuku_payout4",
        "fuku_horse_num5",
        "fuku_payout5",
        "umaren_payout1",
        "sanrenpuku_payout1",
    ]
    payout_select = ",\n            ".join(payout_column(name) for name in payout_fields)
    sql = f"""
        SELECT
            r.race_year, r.race_month_day, r.track_code, r.kaiji, r.nichiji, r.race_num,
            r.race_name, r.race_short10, r.track_type_code, r.distance,
            r.weather_code, r.turf_condition, r.dirt_condition,
            h.horse_num, h.waku_num, h.horse_name, h.finish_order, h.confirmed_order,
            h.win_popularity, h.final_3f, h.leg_quality_code, h.abnormal_code,
            {horse_column("mining_predicted_order")}, {horse_column("burden_weight")},
            {horse_column("horse_weight")}, {horse_column("weight_change_sign", "''")},
            {horse_column("weight_change_diff", "''")},
            COALESCE(NULLIF(hm.sire_name, ''), NULLIF(om.sire_name, ''), '') AS sire_name,
            COALESCE(NULLIF(hm.dam_sire_name, ''), NULLIF(om.dam_sire_name, ''), '') AS dam_sire_name,
            {payout_select}
          FROM races r
          JOIN horse_races h
            ON r.race_year = h.race_year
           AND r.race_month_day = h.race_month_day
           AND r.track_code = h.track_code
           AND r.kaiji = h.kaiji
           AND r.nichiji = h.nichiji
           AND r.race_num = h.race_num
          LEFT JOIN payouts p
            ON r.race_year = p.race_year
           AND r.race_month_day = p.race_month_day
           AND r.track_code = p.track_code
           AND r.kaiji = p.kaiji
           AND r.nichiji = p.nichiji
           AND r.race_num = p.race_num
          LEFT JOIN horse_masters hm
            ON hm.blood_register_num = h.blood_register_num
          LEFT JOIN offspring_master om
            ON om.blood_register_num = h.blood_register_num
         WHERE r.race_year || r.race_month_day = ?
           AND r.track_code IN ({placeholders})
         ORDER BY r.track_code, CAST(r.race_num AS INTEGER), CAST(h.horse_num AS INTEGER)
    """
    return list(conn.execute(sql, [date_key, *TRACK_NAMES.keys()]))


def payout_map(row: sqlite3.Row, prefix: str, slots: int) -> dict[str, int]:
    payouts: dict[str, int] = {}
    for idx in range(1, slots + 1):
        try:
            horse_num_value = row[f"{prefix}_horse_num{idx}"]
            payout_value = row[f"{prefix}_payout{idx}"]
        except (KeyError, IndexError):
            continue
        horse_num = normalize_horse_num(horse_num_value)
        payout = int(payout_value or 0)
        if horse_num and payout > 0:
            payouts[horse_num] = payout
    return payouts


def load_next_races(conn: sqlite3.Connection, date_key: str) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in TRACK_NAMES)
    sql = f"""
        SELECT race_year, race_month_day, track_code, race_num,
               race_name, race_short10, track_type_code, distance,
               turf_condition, dirt_condition, start_time
          FROM races
         WHERE race_year || race_month_day = ?
           AND track_code IN ({placeholders})
         ORDER BY track_code, CAST(race_num AS INTEGER)
    """
    return list(conn.execute(sql, [date_key, *TRACK_NAMES.keys()]))


def load_next_horses(conn: sqlite3.Connection, date_key: str) -> list[NextHorse]:
    placeholders = ",".join("?" for _ in TRACK_NAMES)
    sql = f"""
        SELECT
            r.track_code, r.race_num, r.race_name, r.race_short10,
            r.track_type_code, r.distance, r.start_time,
            h.horse_num, h.blood_register_num, h.waku_num, h.horse_name, h.jockey_code, h.jockey_short_name,
            h.win_odds, h.win_popularity, h.leg_quality_code, h.abnormal_code,
            h.mining_predicted_order,
            COALESCE(NULLIF(hm.sire_name, ''), NULLIF(om.sire_name, ''), '') AS sire_name,
            COALESCE(NULLIF(hm.dam_sire_name, ''), NULLIF(om.dam_sire_name, ''), '') AS dam_sire_name
          FROM races r
          JOIN horse_races h
            ON r.race_year = h.race_year
           AND r.race_month_day = h.race_month_day
           AND r.track_code = h.track_code
           AND r.kaiji = h.kaiji
            AND r.nichiji = h.nichiji
            AND r.race_num = h.race_num
          LEFT JOIN horse_masters hm
            ON hm.blood_register_num = h.blood_register_num
          LEFT JOIN offspring_master om
            ON om.blood_register_num = h.blood_register_num
         WHERE r.race_year || r.race_month_day = ?
           AND r.track_code IN ({placeholders})
           AND TRIM(COALESCE(h.horse_num, '')) NOT IN ('', '00')
           AND COALESCE(NULLIF(TRIM(h.abnormal_code), ''), '0') = '0'
          ORDER BY r.track_code, CAST(r.race_num AS INTEGER), CAST(h.horse_num AS INTEGER)
    """
    horses: list[NextHorse] = []
    for row in conn.execute(sql, [date_key, *TRACK_NAMES.keys()]):
        surface = surface_from_code(row["track_type_code"] or "")
        distance = int(row["distance"] or 0)
        horses.append(
            NextHorse(
                track_code=str(row["track_code"]),
                race_num=int(row["race_num"] or 0),
                race_name=(row["race_name"] or row["race_short10"] or "").strip(),
                surface=surface,
                distance=distance,
                band=distance_band(surface, distance),
                start_time=time_hhmm(str(row["start_time"] or "")),
                horse_num=str(row["horse_num"] or "").lstrip("0") or "0",
                blood_register_num=str(row["blood_register_num"] or "").strip(),
                frame=int(row["waku_num"] or 0),
                name=str(row["horse_name"] or "").strip(),
                jockey_code=str(row["jockey_code"] or "").strip(),
                jockey=str(row["jockey_short_name"] or "").strip(),
                odds=(int(row["win_odds"] or 0) / 10.0),
                popularity=int(row["win_popularity"] or 0),
                style=STYLE_NAMES.get(str(row["leg_quality_code"] or "").strip(), ""),
                mining_rank=int(row["mining_predicted_order"] or 0),
                sire_name=str(row["sire_name"] or "").strip(),
                dam_sire_name=str(row["dam_sire_name"] or "").strip(),
            )
        )
    return horses


def training_data_count(conn: sqlite3.Connection) -> int:
    try:
        return int(conn.execute("SELECT COUNT(*) FROM training_times").fetchone()[0] or 0)
    except sqlite3.Error:
        return 0


def training_trend_notes(conn: sqlite3.Connection, date_key: str) -> list[str]:
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
    try:
        rows = list(conn.execute(sql, (date_key, start_date, date_key)))
    except sqlite3.Error:
        rows = []
    top3_rows = [row for row in rows if int(row["is_top3"] or 0) == 1]
    if not top3_rows:
        return [
            "対象日の3着内馬に紐づく直近追い切りデータがDBにありません。",
            "SLOP/WOOD（坂路・ウッド）を取り込めるようにすると、最終追い切りのコース・時計・ラップ傾向を表示できます。",
        ]

    type_counter = Counter(str(r["training_type"] or "不明") for r in top3_rows)
    notes = [f"追い切り取得済み出走馬{len(rows)}頭中、3着内馬{len(top3_rows)}頭分の直近追い切りを比較"]
    notes.extend(training_overall_pattern_notes(top3_rows, rows))
    notes.append("3着内馬の追い切り種別: " + " / ".join(f"{training_type_label(k)}:{v}頭" for k, v in type_counter.most_common()))
    slope_rows = [r for r in top3_rows if str(r["training_type"] or "") == "slope"]
    wood_rows = [r for r in top3_rows if str(r["training_type"] or "") == "wood"]
    if slope_rows:
        notes.append(
            "参考時計 坂路平均: "
            f"4F {_avg_training_time(slope_rows, 'time_4f')} / "
            f"3F {_avg_training_time(slope_rows, 'time_3f')} / "
            f"2F {_avg_training_time(slope_rows, 'time_2f')} / "
            f"1F {_avg_training_time(slope_rows, 'lap_1f')}"
        )
        notes.append(
            "参考ラップ 坂路平均: "
            f"800-600 {_avg_training_time(slope_rows, 'lap_4f_3f')} / "
            f"600-400 {_avg_training_time(slope_rows, 'lap_3f_2f')} / "
            f"400-200 {_avg_training_time(slope_rows, 'lap_2f_1f')} / "
            f"200-0 {_avg_training_time(slope_rows, 'lap_1f')}"
        )
    if wood_rows:
        notes.append(
            "参考時計 ウッド平均: "
            f"6F {_avg_training_time(wood_rows, 'time_6f')} / "
            f"5F {_avg_training_time(wood_rows, 'time_5f')} / "
            f"4F {_avg_training_time(wood_rows, 'time_4f')} / "
            f"1F {_avg_training_time(wood_rows, 'lap_1f')}"
        )
        notes.append(
            "参考ラップ ウッド終い4F平均: "
            f"800-600 {_avg_training_time(wood_rows, 'lap_4f_3f')} / "
            f"600-400 {_avg_training_time(wood_rows, 'lap_3f_2f')} / "
            f"400-200 {_avg_training_time(wood_rows, 'lap_2f_1f')} / "
            f"200-0 {_avg_training_time(wood_rows, 'lap_1f')}"
        )
    return notes


def load_training_by_race(conn: sqlite3.Connection, date_key: str) -> dict[tuple[str, int], list[dict]]:
    start_date = (datetime.strptime(date_key, "%Y%m%d") - timedelta(days=10)).strftime("%Y%m%d")
    sql = """
        WITH top_horses AS (
            SELECT
                h.track_code,
                h.race_num,
                h.horse_name,
                h.blood_register_num,
                CASE
                    WHEN CAST(COALESCE(h.confirmed_order, 0) AS INTEGER) > 0
                    THEN CAST(COALESCE(h.confirmed_order, 0) AS INTEGER)
                    ELSE CAST(COALESCE(h.finish_order, 0) AS INTEGER)
                END AS finish_order
              FROM horse_races h
             WHERE h.race_year || h.race_month_day = ?
               AND TRIM(COALESCE(h.blood_register_num, '')) != ''
               AND COALESCE(NULLIF(TRIM(h.abnormal_code), ''), '0') = '0'
               AND (
                    CAST(COALESCE(h.confirmed_order, 0) AS INTEGER) BETWEEN 1 AND 3
                    OR CAST(COALESCE(h.finish_order, 0) AS INTEGER) BETWEEN 1 AND 3
               )
        ),
        ranked AS (
            SELECT
                th.track_code,
                th.race_num,
                th.horse_name,
                th.finish_order,
                t.training_date,
                t.training_type,
                t.training_center_code,
                t.course_code,
                t.course_direction,
                t.times_total,
                t.time_10f,
                t.time_9f,
                t.time_8f,
                t.time_7f,
                t.time_6f,
                t.time_5f,
                t.time_4f,
                t.time_3f,
                t.time_2f,
                t.lap_10f_9f,
                t.lap_9f_8f,
                t.lap_8f_7f,
                t.lap_7f_6f,
                t.lap_6f_5f,
                t.lap_5f_4f,
                t.lap_4f_3f,
                t.lap_3f_2f,
                t.lap_2f_1f,
                t.lap_1f,
                ROW_NUMBER() OVER (
                    PARTITION BY th.track_code, th.race_num, th.blood_register_num
                    ORDER BY t.training_date DESC, t.training_time_str DESC
                ) AS rn
              FROM top_horses th
              JOIN training_times t ON t.blood_register_num = th.blood_register_num
             WHERE t.training_date BETWEEN ? AND ?
        )
        SELECT *
          FROM ranked
         WHERE rn = 1
         ORDER BY track_code, CAST(race_num AS INTEGER), finish_order
    """
    out: dict[tuple[str, int], list[dict]] = defaultdict(list)
    try:
        rows = conn.execute(sql, (date_key, start_date, date_key)).fetchall()
    except sqlite3.Error:
        rows = []
    for row in rows:
        key = (str(row["track_code"]), int(row["race_num"] or 0))
        out[key].append(
            {
                "horse_name": str(row["horse_name"] or "").strip(),
                "finish_order": int(row["finish_order"] or 0),
                "training_date": str(row["training_date"] or ""),
                "training_type": str(row["training_type"] or ""),
                "training_center_code": str(row["training_center_code"] or ""),
                "course_code": str(row["course_code"] or ""),
                "course_direction": str(row["course_direction"] or ""),
                "times_total": int(row["times_total"] or 0),
                "time_10f": int(row["time_10f"] or 0),
                "time_9f": int(row["time_9f"] or 0),
                "time_8f": int(row["time_8f"] or 0),
                "time_7f": int(row["time_7f"] or 0),
                "time_6f": int(row["time_6f"] or 0),
                "time_5f": int(row["time_5f"] or 0),
                "time_4f": int(row["time_4f"] or 0),
                "time_3f": int(row["time_3f"] or 0),
                "time_2f": int(row["time_2f"] or 0),
                "lap_10f_9f": int(row["lap_10f_9f"] or 0),
                "lap_9f_8f": int(row["lap_9f_8f"] or 0),
                "lap_8f_7f": int(row["lap_8f_7f"] or 0),
                "lap_7f_6f": int(row["lap_7f_6f"] or 0),
                "lap_6f_5f": int(row["lap_6f_5f"] or 0),
                "lap_5f_4f": int(row["lap_5f_4f"] or 0),
                "lap_4f_3f": int(row["lap_4f_3f"] or 0),
                "lap_3f_2f": int(row["lap_3f_2f"] or 0),
                "lap_2f_1f": int(row["lap_2f_1f"] or 0),
                "lap_1f": int(row["lap_1f"] or 0),
            }
        )
    return out


def training_type_label(value: str) -> str:
    return "坂路" if value == "slope" else "ウッド" if value == "wood" else value or "不明"


def training_time_text(value: int) -> str:
    return f"{value / 10:.1f}秒" if value > 0 else "時計未取得"


def _row_int(row: sqlite3.Row | dict, key: str) -> int:
    try:
        value = row[key]
    except (KeyError, IndexError):
        return 0
    return int(value or 0)


def _row_str(row: sqlite3.Row | dict, key: str) -> str:
    try:
        value = row[key]
    except (KeyError, IndexError):
        return ""
    return str(value or "").strip()


def _avg_training_time(rows: list[sqlite3.Row] | list[dict], key: str) -> str:
    values = [_row_int(row, key) for row in rows]
    values = [v for v in values if v > 0]
    if not values:
        return "-"
    return f"{sum(values) / len(values) / 10:.1f}秒"


def _training_fast_finish(row: sqlite3.Row | dict) -> bool:
    last1 = _row_int(row, "lap_1f") or _row_int(row, "times_last_200m")
    if not last1:
        return False
    training_type = _row_str(row, "training_type")
    return last1 <= (125 if training_type == "slope" else 120)


def _training_good_finish(row: sqlite3.Row | dict) -> bool:
    last1 = _row_int(row, "lap_1f") or _row_int(row, "times_last_200m")
    if not last1:
        return False
    training_type = _row_str(row, "training_type")
    return last1 <= (130 if training_type == "slope" else 130)


def _training_accelerated(row: sqlite3.Row | dict) -> bool:
    last1 = _row_int(row, "lap_1f") or _row_int(row, "times_last_200m")
    prev = _row_int(row, "lap_2f_1f")
    return bool(last1 and prev and last1 <= prev and _training_acceleration_base_met(row))


def _training_acceleration_base_met(row: sqlite3.Row | dict) -> bool:
    last1 = _row_int(row, "lap_1f") or _row_int(row, "times_last_200m")
    training_type = _row_str(row, "training_type")
    if training_type == "slope":
        return (
            0 < last1 <= 135
            or 0 < _row_int(row, "time_4f") <= 560
        )
    if training_type == "wood":
        return (
            0 < last1 <= 130
            or 0 < _row_int(row, "time_6f") <= 880
            or 0 < _row_int(row, "time_5f") <= 700
            or 0 < _row_int(row, "time_4f") <= 550
        )
    return False


def _training_slowed(row: sqlite3.Row | dict) -> bool:
    last1 = _row_int(row, "lap_1f") or _row_int(row, "times_last_200m")
    prev = _row_int(row, "lap_2f_1f")
    return bool(last1 and prev and last1 >= prev + 8)


def _training_fast_total(row: sqlite3.Row | dict) -> bool:
    training_type = _row_str(row, "training_type")
    if training_type == "slope":
        return 0 < _row_int(row, "time_4f") <= 540
    if training_type == "wood":
        return (0 < _row_int(row, "time_6f") <= 850) or (0 < _row_int(row, "time_5f") <= 680)
    return False


def _training_long_work(row: sqlite3.Row | dict) -> bool:
    return _row_str(row, "training_type") == "wood" and _row_int(row, "time_6f") > 0


def _training_light_finish_focus(row: sqlite3.Row | dict) -> bool:
    training_type = _row_str(row, "training_type")
    if training_type == "slope":
        return _row_int(row, "time_4f") >= 630 and _training_good_finish(row)
    if training_type == "wood":
        return _row_int(row, "time_6f") == 0 and _row_int(row, "time_5f") == 0 and _training_good_finish(row)
    return False


def training_pattern_tags(row: sqlite3.Row | dict) -> list[str]:
    tags: list[str] = []
    if _training_fast_finish(row):
        tags.append("終い鋭い")
    elif _training_good_finish(row):
        tags.append("終い良好")
    if _training_accelerated(row):
        tags.append("加速ラップ")
    elif _training_slowed(row):
        tags.append("終い失速")
    if _training_fast_total(row):
        tags.append("全体時計速い")
    if _training_long_work(row):
        tags.append("長め負荷")
    if _training_light_finish_focus(row):
        tags.append("終い重点")
    if not tags:
        tags.append("標準的")
    return tags


def training_pattern_text(row: sqlite3.Row | dict) -> str:
    tags = training_pattern_tags(row)
    return "・".join(tags[:3])


def race_training_trend_text(rows: list[dict]) -> str:
    if not rows:
        return "調教データなし"
    checks = [
        ("終い良好", _training_good_finish),
        ("加速ラップ", _training_accelerated),
        ("全体時計速い", _training_fast_total),
        ("長め負荷", _training_long_work),
        ("終い重点", _training_light_finish_focus),
    ]
    parts: list[str] = []
    total = len(rows)
    for label, fn in checks:
        count = sum(1 for row in rows if fn(row))
        if count >= 2 or (total == 1 and count == 1):
            parts.append(f"{label}{count}/{total}")
    if parts:
        if len(parts) > 3:
            return " / ".join(parts[:3]) + " ほか"
        return " / ".join(parts)

    types = Counter(training_type_label(_row_str(row, "training_type")) for row in rows)
    type_text = " / ".join(f"{k}{v}/{total}" for k, v in types.most_common())
    tag_counter: Counter[str] = Counter()
    for row in rows:
        tag_counter.update(training_pattern_tags(row))
    if tag_counter["標準的"] == total:
        return f"標準的中心（{type_text}）"
    if len(types) == 1:
        return f"強い型なし（{type_text}）"
    return f"型ばらつき（{type_text}）"


def training_overall_pattern_notes(rows: list[sqlite3.Row], baseline_rows: list[sqlite3.Row] | None = None) -> list[str]:
    if not rows:
        return []
    total = len(rows)
    def count(fn) -> int:
        return sum(1 for row in rows if fn(row))

    def compare_text(label: str, fn) -> str:
        top_count = count(fn)
        if not baseline_rows:
            return f"{label}{top_count}/{total}({pct(top_count, total)})"
        base_total = len(baseline_rows)
        base_count = sum(1 for row in baseline_rows if fn(row))
        return f"{label}{top_count}/{total}({pct(top_count, total)}) / 全体{base_count}/{base_total}({pct(base_count, base_total)})"

    notes = [
        "比較: "
        + "、".join(
            [
                compare_text("終い良好", _training_good_finish),
                compare_text("加速ラップ", _training_accelerated),
            ]
        )
        + "。",
        "負荷傾向: "
        + "、".join(
            [
                compare_text("全体速い", _training_fast_total),
                compare_text("長め負荷", _training_long_work),
                compare_text("終い重点", _training_light_finish_focus),
            ]
        )
        + "。",
        "判定基準: 加速ラップは終いまたは全体時計が基準内のものだけ集計。",
    ]
    tag_counter: Counter[str] = Counter()
    for row in rows:
        tag_counter.update(training_pattern_tags(row))
    short_label = {"全体時計速い": "全体速い", "加速ラップ": "加速"}
    top_tags = [f"{short_label.get(tag, tag)}{count}" for tag, count in tag_counter.most_common(3)]
    if top_tags:
        notes.append("多かった型: " + " / ".join(top_tags))
    return notes


def training_center_label(value: str) -> str:
    return "美浦" if value == "0" else "栗東" if value == "1" else value or "-"


def wood_course_label(course_code: str, direction: str) -> str:
    course = {"0": "A", "1": "B", "2": "C", "3": "D", "4": "E"}.get(course_code, course_code or "-")
    around = {"0": "右", "1": "左"}.get(direction, direction or "")
    return f"{course}{around}"


def training_course_text(row: dict) -> str:
    center = training_center_label(str(row.get("training_center_code") or ""))
    if str(row.get("training_type") or "") == "wood":
        return f"{center} W{wood_course_label(str(row.get('course_code') or ''), str(row.get('course_direction') or ''))}"
    return f"{center} 坂路"


def training_work_summary(row: dict) -> str:
    if str(row.get("training_type") or "") == "wood":
        parts = [
            ("6F", int(row.get("time_6f") or 0)),
            ("5F", int(row.get("time_5f") or 0)),
            ("4F", int(row.get("time_4f") or 0)),
            ("3F", int(row.get("time_3f") or 0)),
            ("2F", int(row.get("time_2f") or 0)),
            ("1F", int(row.get("lap_1f") or 0)),
        ]
    else:
        parts = [
            ("4F", int(row.get("time_4f") or row.get("times_total") or 0)),
            ("3F", int(row.get("time_3f") or row.get("times_last_600m") or 0)),
            ("2F", int(row.get("time_2f") or row.get("times_last_400m") or 0)),
            ("1F", int(row.get("lap_1f") or row.get("times_last_200m") or 0)),
        ]
    values = [f"{label} {training_time_text(value)}" for label, value in parts if value > 0]
    return " / ".join(values) if values else training_time_text(int(row.get("times_total") or 0))


def training_lap_text(row: dict) -> str:
    if str(row.get("training_type") or "") == "wood":
        parts = [
            ("800-600", int(row.get("lap_4f_3f") or 0)),
            ("600-400", int(row.get("lap_3f_2f") or 0)),
            ("400-200", int(row.get("lap_2f_1f") or 0)),
            ("200-0", int(row.get("lap_1f") or 0)),
        ]
    else:
        parts = [
            ("800-600", int(row.get("lap_4f_3f") or 0)),
            ("600-400", int(row.get("lap_3f_2f") or 0)),
            ("400-200", int(row.get("lap_2f_1f") or 0)),
            ("200-0", int(row.get("lap_1f") or 0)),
        ]
    values = [f"{label} {training_time_text(value)}" for label, value in parts if value > 0]
    return " / ".join(values) if values else "ラップ未取得"


def training_row_to_dict(row: sqlite3.Row) -> dict:
    keys = set(row.keys())

    def value(name: str, default=0):
        return row[name] if name in keys else default

    def int_value(name: str, default=0) -> int:
        return int(value(name, default) or 0)

    training_type = str(value("training_type", "") or "")
    course_code = str(value("course_code", "") or "")
    center_code = str(value("training_center_code", "") or "")
    if not center_code and training_type == "slope" and len(course_code) == 1:
        center_code = course_code

    return {
        "training_date": str(value("training_date", "") or ""),
        "training_type": training_type,
        "training_center_code": center_code,
        "course_code": course_code,
        "course_direction": str(value("course_direction", "") or ""),
        "times_total": int_value("times_total"),
        "time_10f": int_value("time_10f"),
        "time_9f": int_value("time_9f"),
        "time_8f": int_value("time_8f"),
        "time_7f": int_value("time_7f"),
        "time_6f": int_value("time_6f"),
        "time_5f": int_value("time_5f"),
        "time_4f": int_value("time_4f", int_value("times_total")),
        "time_3f": int_value("time_3f", int_value("times_last_600m")),
        "time_2f": int_value("time_2f", int_value("times_last_400m")),
        "lap_10f_9f": int_value("lap_10f_9f"),
        "lap_9f_8f": int_value("lap_9f_8f"),
        "lap_8f_7f": int_value("lap_8f_7f"),
        "lap_7f_6f": int_value("lap_7f_6f"),
        "lap_6f_5f": int_value("lap_6f_5f"),
        "lap_5f_4f": int_value("lap_5f_4f"),
        "lap_4f_3f": int_value("lap_4f_3f", int_value("times_last_600m")),
        "lap_3f_2f": int_value("lap_3f_2f", int_value("times_last_400m")),
        "lap_2f_1f": int_value("lap_2f_1f", int_value("times_last_200m")),
        "lap_1f": int_value("lap_1f", int_value("lap_last_300m") or int_value("times_last_200m")),
    }


def _surface_code_range(surface: str) -> tuple[int, int] | None:
    if surface == "芝":
        return (10, 22)
    if surface == "ダート":
        return (23, 29)
    if surface == "障害":
        return (51, 59)
    return None


def attach_jockey_course_stats(
    conn: sqlite3.Connection,
    horses: list[NextHorse],
    before_date: str,
) -> None:
    if not horses:
        return

    tracks = sorted({h.track_code for h in horses if h.track_code})
    distances = sorted({h.distance for h in horses if h.distance})
    codes = sorted({h.jockey_code for h in horses if h.jockey_code})
    names = sorted({h.jockey for h in horses if h.jockey})
    if not tracks or not distances or (not codes and not names):
        for horse in horses:
            horse.jockey_course = JockeyCourseStats()
        return

    track_ph = ",".join("?" for _ in tracks)
    dist_ph = ",".join("?" for _ in distances)
    jockey_clauses: list[str] = []
    params: list[object] = [before_date, *tracks, *distances]
    if codes:
        code_ph = ",".join("?" for _ in codes)
        jockey_clauses.append(f"h.jockey_code IN ({code_ph})")
        params.extend(codes)
    if names:
        name_ph = ",".join("?" for _ in names)
        jockey_clauses.append(f"h.jockey_short_name IN ({name_ph})")
        params.extend(names)

    sql = f"""
        SELECT
            r.track_code, r.track_type_code, r.distance,
            h.jockey_code, h.jockey_short_name,
            CAST(COALESCE(h.confirmed_order, h.finish_order, 0) AS INTEGER) AS confirmed_order,
            CAST(COALESCE(h.finish_order, h.confirmed_order, 0) AS INTEGER) AS finish_order
          FROM races r
          JOIN horse_races h
            ON r.race_year = h.race_year
           AND r.race_month_day = h.race_month_day
           AND r.track_code = h.track_code
           AND r.kaiji = h.kaiji
           AND r.nichiji = h.nichiji
           AND r.race_num = h.race_num
         WHERE r.race_year || r.race_month_day < ?
           AND r.track_code IN ({track_ph})
           AND CAST(r.distance AS INTEGER) IN ({dist_ph})
           AND TRIM(COALESCE(h.horse_num, '')) NOT IN ('', '00')
           AND (
                CAST(COALESCE(h.confirmed_order, 0) AS INTEGER) > 0
                OR CAST(COALESCE(h.finish_order, 0) AS INTEGER) > 0
           )
           AND ({" OR ".join(jockey_clauses)})
    """

    aggregate: dict[tuple[str, str, str, int], JockeyCourseStats] = defaultdict(JockeyCourseStats)
    for row in conn.execute(sql, params):
        surface = surface_from_code(str(row["track_type_code"] or ""))
        distance = int(row["distance"] or 0)
        order = int(row["confirmed_order"] or 0) or int(row["finish_order"] or 0)
        keys: list[tuple[str, str, str, int]] = []
        jockey_code = str(row["jockey_code"] or "").strip()
        jockey_name = str(row["jockey_short_name"] or "").strip()
        if jockey_code in codes:
            keys.append((jockey_code, str(row["track_code"]), surface, distance))
        if jockey_name in names:
            keys.append((jockey_name, str(row["track_code"]), surface, distance))
        for key in keys:
            stats = aggregate[key]
            stats.starts += 1
            if order == 1:
                stats.wins += 1
            if 1 <= order <= 3:
                stats.top3 += 1

    for horse in horses:
        jockey_key = horse.jockey_code or horse.jockey
        key = (jockey_key, horse.track_code, horse.surface, horse.distance)
        horse.jockey_course = aggregate.get(key, JockeyCourseStats())


def attach_next_training(conn: sqlite3.Connection, horses: list[NextHorse], target_date: str) -> None:
    blood_nums = sorted({h.blood_register_num for h in horses if h.blood_register_num})
    if not blood_nums:
        return

    start_date = (datetime.strptime(target_date, "%Y%m%d") - timedelta(days=10)).strftime("%Y%m%d")
    blood_ph = ",".join("?" for _ in blood_nums)
    sql = f"""
        WITH ranked AS (
            SELECT
                t.*,
                ROW_NUMBER() OVER (
                    PARTITION BY t.blood_register_num
                    ORDER BY t.training_date DESC, t.training_time_str DESC
                ) AS rn
              FROM training_times t
             WHERE t.blood_register_num IN ({blood_ph})
               AND t.training_date BETWEEN ? AND ?
        )
        SELECT *
          FROM ranked
         WHERE rn = 1
    """
    try:
        rows = conn.execute(sql, [*blood_nums, start_date, target_date]).fetchall()
    except sqlite3.Error:
        rows = []

    by_blood = {str(row["blood_register_num"] or "").strip(): training_row_to_dict(row) for row in rows}
    for horse in horses:
        horse.training = by_blood.get(horse.blood_register_num)


def next_available_race_date(conn: sqlite3.Connection, after_date: str, min_date: str | None = None) -> str | None:
    placeholders = ",".join("?" for _ in TRACK_NAMES)
    min_date = min_date or "00000000"
    sql = f"""
        SELECT race_year || race_month_day AS d, COUNT(*) AS race_count
          FROM races
         WHERE race_year || race_month_day > ?
           AND race_year || race_month_day >= ?
           AND track_code IN ({placeholders})
         GROUP BY d
         ORDER BY d
         LIMIT 1
    """
    row = conn.execute(sql, [after_date, min_date, *TRACK_NAMES.keys()]).fetchone()
    return str(row["d"]) if row else None


def build_races(rows: list[sqlite3.Row]) -> list[RaceResult]:
    grouped: dict[tuple, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        if not is_normal_starter_row(row):
            continue
        key = (
            row["race_year"],
            row["race_month_day"],
            row["track_code"],
            row["kaiji"],
            row["nichiji"],
            row["race_num"],
        )
        grouped[key].append(row)

    races: list[RaceResult] = []
    for key, race_rows in grouped.items():
        first = race_rows[0]
        surface = surface_from_code(first["track_type_code"] or "")
        distance = int(first["distance"] or 0)
        ground_code = first["turf_condition"] if surface == "芝" else first["dirt_condition"]
        horses = [
            HorseResult(
                horse_num=normalize_horse_num(row["horse_num"]) or "0",
                frame=int(row["waku_num"] or 0),
                name=str(row["horse_name"] or "").strip(),
                order=result_order(row),
                popularity=int(row["win_popularity"] or 0),
                final3f=int(row["final_3f"] or 0),
                style=STYLE_NAMES.get(str(row["leg_quality_code"] or "").strip(), ""),
                sire_name=str(row["sire_name"] or "").strip(),
                dam_sire_name=str(row["dam_sire_name"] or "").strip(),
                mining_rank=row_int(row, "mining_predicted_order"),
                burden_weight=row_int(row, "burden_weight"),
                horse_weight=row_int(row, "horse_weight"),
                weight_change=weight_change_kg(
                    row_value(row, "weight_change_sign"),
                    row_value(row, "weight_change_diff"),
                ),
            )
            for row in race_rows
        ]
        assign_final3f_rank(horses)
        if not [horse for horse in horses if 1 <= horse.order <= 3]:
            continue
        races.append(
            RaceResult(
                date=f"{first['race_year']}{first['race_month_day']}",
                track_code=str(first["track_code"]),
                race_num=int(first["race_num"] or 0),
                race_name=(first["race_name"] or first["race_short10"] or "").strip(),
                surface=surface,
                distance=distance,
                band=distance_band(surface, distance),
                weather=WEATHER_NAMES.get(str(first["weather_code"] or ""), ""),
                ground=GROUND_NAMES.get(str(ground_code or ""), ""),
                horses=horses,
                tan_payout=int(first["tan_payout1"] or 0),
                umaren_payout=int(first["umaren_payout1"] or 0),
                sanrenpuku_payout=int(first["sanrenpuku_payout1"] or 0),
                tan_payouts=payout_map(first, "tan", 3),
                fuku_payouts=payout_map(first, "fuku", 5),
            )
        )
    return sorted(races, key=lambda r: (r.track_code, r.race_num))


def assign_final3f_rank(horses: list[HorseResult]) -> None:
    ranked = sorted([h for h in horses if h.final3f > 0], key=lambda h: h.final3f)
    prev_time = None
    prev_rank = 0
    for idx, horse in enumerate(ranked, start=1):
        if horse.final3f == prev_time:
            horse.final3f_rank = prev_rank
        else:
            horse.final3f_rank = idx
            prev_rank = idx
            prev_time = horse.final3f


def summarize(label: str, races: list[RaceResult]) -> TrendStats:
    starter_frames: Counter = Counter()
    starter_buckets: Counter = Counter()
    starter_styles: Counter = Counter()
    winner_frames: Counter = Counter()
    top3_frames: Counter = Counter()
    winner_buckets: Counter = Counter()
    top3_buckets: Counter = Counter()
    winner_styles: Counter = Counter()
    top3_styles: Counter = Counter()
    winner_popularities: list[int] = []
    top3_popularities: list[int] = []
    fast3f_top3 = 0
    starter_count = 0
    top3_count = 0
    high_payout_races = 0

    for race in races:
        winner = race.winner
        if not winner:
            continue
        for horse in race.horses:
            starter_count += 1
            starter_frames[horse.frame] += 1
            starter_buckets[frame_bucket(horse.frame)] += 1
            if horse.style:
                starter_styles[horse.style] += 1
        winner_frames[winner.frame] += 1
        winner_buckets[frame_bucket(winner.frame)] += 1
        if winner.style:
            winner_styles[winner.style] += 1
        if winner.popularity:
            winner_popularities.append(winner.popularity)
        if race.sanrenpuku_payout >= 10_000 or race.umaren_payout >= 5_000 or race.tan_payout >= 1_000:
            high_payout_races += 1
        for horse in race.top3:
            top3_count += 1
            top3_frames[horse.frame] += 1
            top3_buckets[frame_bucket(horse.frame)] += 1
            if horse.style:
                top3_styles[horse.style] += 1
            if horse.popularity:
                top3_popularities.append(horse.popularity)
            if 0 < horse.final3f_rank <= 3:
                fast3f_top3 += 1

    return TrendStats(
        label=label,
        races=races,
        starter_count=starter_count,
        starter_frames=starter_frames,
        starter_buckets=starter_buckets,
        starter_styles=starter_styles,
        winner_frames=winner_frames,
        top3_frames=top3_frames,
        winner_buckets=winner_buckets,
        top3_buckets=top3_buckets,
        winner_styles=winner_styles,
        top3_styles=top3_styles,
        winner_popularities=winner_popularities,
        top3_popularities=top3_popularities,
        fast3f_top3=fast3f_top3,
        top3_count=top3_count,
        high_payout_races=high_payout_races,
    )


def summarize_bloodlines(races: list[RaceResult]) -> BloodlineStats:
    sire_starters: Counter = Counter()
    sire_top3: Counter = Counter()
    sire_wins: Counter = Counter()
    dam_sire_starters: Counter = Counter()
    dam_sire_top3: Counter = Counter()
    dam_sire_wins: Counter = Counter()
    starter_count = 0
    top3_count = 0
    for race in races:
        winner = race.winner
        for horse in race.horses:
            starter_count += 1
            if horse.sire_name:
                sire_starters[horse.sire_name] += 1
            if horse.dam_sire_name:
                dam_sire_starters[horse.dam_sire_name] += 1
        if winner:
            if winner.sire_name:
                sire_wins[winner.sire_name] += 1
            if winner.dam_sire_name:
                dam_sire_wins[winner.dam_sire_name] += 1
        for horse in race.top3:
            top3_count += 1
            if horse.sire_name:
                sire_top3[horse.sire_name] += 1
            if horse.dam_sire_name:
                dam_sire_top3[horse.dam_sire_name] += 1
    return BloodlineStats(
        sire_starters=sire_starters,
        sire_top3=sire_top3,
        sire_wins=sire_wins,
        dam_sire_starters=dam_sire_starters,
        dam_sire_top3=dam_sire_top3,
        dam_sire_wins=dam_sire_wins,
        starter_count=starter_count,
        top3_count=top3_count,
        race_count=len(races),
    )


def bloodline_notes(stats: BloodlineStats) -> list[str]:
    notes: list[str] = []
    if stats.top3_count <= 0:
        return ["血統データなし"]
    overall_rate = stats.top3_count / stats.starter_count if stats.starter_count else 0.0

    def enough_sample(name: str, count: int, starters: Counter) -> bool:
        starter_count = starters[name]
        rate = count / starter_count if starter_count else 0.0
        return (
            count >= MIN_BLOODLINE_TOP3
            and starter_count >= MIN_BLOODLINE_STARTERS
            and rate >= overall_rate + MIN_BLOODLINE_RATE_EDGE
        )

    sire_items = sorted(
        [(name, count) for name, count in stats.sire_top3.items() if enough_sample(name, count, stats.sire_starters)],
        key=lambda item: (item[1], item[1] / max(stats.sire_starters[item[0]], 1)),
        reverse=True,
    )
    for name, count in sire_items[:3]:
        starters = stats.sire_starters[name]
        win_count = stats.sire_wins[name]
        win_text = f"、{win_count}勝" if win_count else ""
        notes.append(f"父 {name}: 3着内{count}/{starters}頭（{pct(count, starters)}）{win_text}")
    dam_sire_items = sorted(
        [(name, count) for name, count in stats.dam_sire_top3.items() if enough_sample(name, count, stats.dam_sire_starters)],
        key=lambda item: (item[1], item[1] / max(stats.dam_sire_starters[item[0]], 1)),
        reverse=True,
    )
    for name, count in dam_sire_items[:3]:
        starters = stats.dam_sire_starters[name]
        win_count = stats.dam_sire_wins[name]
        win_text = f"、{win_count}勝" if win_count else ""
        notes.append(f"母父 {name}: 3着内{count}/{starters}頭（{pct(count, starters)}）{win_text}")
    return notes or [
        "血統は表示できるサンプル不足"
        f"（出走{MIN_BLOODLINE_STARTERS}頭以上、3着内{MIN_BLOODLINE_TOP3}頭以上、"
        f"全体比+{MIN_BLOODLINE_RATE_EDGE * 100:.0f}pt以上のみ表示）"
    ]


def bloodline_groups(
    by_track_surface: dict[tuple[str, str], list[RaceResult]],
) -> list[tuple[str, list[str]]]:
    groups: list[tuple[str, list[str]]] = []
    for (track_code, surface), group in sorted(by_track_surface.items()):
        label = f"{TRACK_NAMES.get(track_code, track_code)} {surface}"
        groups.append((label, bloodline_notes(summarize_bloodlines(group))))
    return groups


def all_starters(races: list[RaceResult]) -> list[HorseResult]:
    return [horse for race in races for horse in race.horses]


def top3_rate(top3_count: int, starter_count: int) -> float:
    return top3_count / starter_count if starter_count else 0.0


def weight_label(value: float) -> str:
    if value <= 0:
        return "無効"
    if value >= 1.18:
        return "高"
    if value >= 1.05:
        return "やや高"
    if value <= 0.85:
        return "低"
    if value < 0.98:
        return "やや低"
    return "標準"


def weight_multiplier_text(value: float) -> str:
    return f"{value:.2f}倍"


def determine_trend_weights(races: list[RaceResult]) -> TrendWeights:
    stats = summarize("全体", races)
    weights = TrendWeights()
    notes: list[str] = []
    if not trend_sample_sufficient(stats):
        weights.notes = ["サンプル不足のため要素別重みは標準寄せ"]
        return weights

    overall_rate = top3_rate(stats.top3_count, stats.starter_count)
    bucket_edges: list[tuple[str, float, int, int]] = []
    for bucket, starters in stats.starter_buckets.items():
        if bucket == "不明" or starters <= 0:
            continue
        count = stats.top3_buckets[bucket]
        bucket_edges.append((bucket, top3_rate(count, starters) - overall_rate, count, starters))
    if bucket_edges:
        bucket, edge, count, starters = max(bucket_edges, key=lambda item: (item[1], item[2]))
        if count >= 5 and edge >= 0.08:
            weights.frame = 1.20
            notes.append(f"枠: {bucket}枠が全体比+{edge * 100:.0f}pt（{count}/{starters}頭）")
        elif count >= 4 and edge >= 0.05:
            weights.frame = 1.10
            notes.append(f"枠: {bucket}枠がやや優勢（{count}/{starters}頭）")
        else:
            weights.frame = 0.90
            notes.append("枠: 強い偏りなし")

    front_count = stats.top3_styles["逃げ"] + stats.top3_styles["先行"]
    front_starters = stats.starter_styles["逃げ"] + stats.starter_styles["先行"]
    late_count = stats.top3_styles["差し"] + stats.top3_styles["追込"]
    late_starters = stats.starter_styles["差し"] + stats.starter_styles["追込"]
    style_edges = []
    if front_starters:
        style_edges.append(("前目", top3_rate(front_count, front_starters) - overall_rate, front_count, front_starters))
    if late_starters:
        style_edges.append(("差し寄り", top3_rate(late_count, late_starters) - overall_rate, late_count, late_starters))
    if style_edges:
        style, edge, count, starters = max(style_edges, key=lambda item: (item[1], item[2]))
        if count >= 8 and edge >= 0.10:
            weights.style = 1.25
            notes.append(f"脚質: {style}が強め（{count}/{starters}頭）")
        elif count >= 6 and edge >= 0.06:
            weights.style = 1.12
            notes.append(f"脚質: {style}がやや優勢（{count}/{starters}頭）")
        else:
            weights.style = 0.92
            notes.append("脚質: 強い偏りなし")

    avg_pop = sum(stats.winner_popularities) / len(stats.winner_popularities) if stats.winner_popularities else 0.0
    high_payout_rate = stats.high_payout_races / len(stats.races) if stats.races else 0.0
    if avg_pop and avg_pop <= 3.2 and high_payout_rate < 0.30:
        weights.market = 1.15
        notes.append(f"人気/オッズ: 堅め寄り（勝ち馬平均{avg_pop:.1f}人気）")
    elif avg_pop >= 5.0 or high_payout_rate >= 0.30:
        weights.market = 0.98
        weights.payout = 1.12
        notes.append(f"荒れ/配当: 中穴許容（高配当{stats.high_payout_races}/{len(stats.races)}R）")
    else:
        weights.market = 1.00
        notes.append("人気/オッズ: 標準")

    starters = all_starters(races)
    mining_starters = [h for h in starters if 1 <= h.mining_rank <= 3]
    mining_top3 = [h for h in mining_starters if 1 <= h.order <= 3]
    if len(mining_starters) >= 12:
        mining_rate = top3_rate(len(mining_top3), len(mining_starters))
        if mining_rate >= overall_rate + 0.10:
            weights.mining = 1.18
            notes.append(f"データマイニング: 上位3位が好調（{len(mining_top3)}/{len(mining_starters)}頭）")
        elif mining_rate <= overall_rate - 0.08:
            weights.mining = 0.85
            notes.append(f"データマイニング: 上位3位が弱め（{len(mining_top3)}/{len(mining_starters)}頭）")
        else:
            notes.append("データマイニング: 標準")
    else:
        notes.append("データマイニング: 前日内サンプル不足で標準")

    blood_notes = bloodline_notes(summarize_bloodlines(races))
    if any("サンプル不足" not in note and "データなし" not in note for note in blood_notes):
        weights.bloodline = 1.08
        notes.append("血統: 表示条件を満たす系統あり")
    else:
        weights.bloodline = 0.85
        notes.append("血統: サンプル不足寄り")

    weights.training = 0.95
    notes.append("調教: 個別補助として弱めに反映")
    weights.jockey = 1.00
    notes.append("騎手同コース: 長期成績のため標準")
    weights.notes = notes
    return weights


def parse_percent(value: str) -> float | None:
    text = str(value or "").strip().replace("%", "")
    if not text:
        return None
    try:
        return float(text) / 100.0
    except ValueError:
        return None


def historical_adjustment_from_rate(rate: float, sample_note_text: str, signal_type: str = "") -> float:
    if signal_type == "bloodline":
        if rate >= 0.75:
            raw = 1.04
        elif rate >= 0.65:
            raw = 0.96
        elif rate >= 0.55:
            raw = 0.82
        else:
            raw = 0.75
        if sample_note_text != "通常":
            return 1.0 + (raw - 1.0) * 0.5
        return raw
    if rate >= 0.80:
        raw = 1.10
    elif rate >= 0.65:
        raw = 1.04
    elif rate >= 0.50:
        raw = 0.96
    else:
        raw = 0.86
    if sample_note_text != "通常":
        return 1.0 + (raw - 1.0) * 0.5
    return raw


def apply_historical_validation_weights(weights: TrendWeights, summary_csv_path: Path) -> None:
    if not summary_csv_path.exists():
        weights.notes.append("過去検証: サマリCSVなし（前日重みのみ）")
        return

    try:
        with summary_csv_path.open("r", newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        weights.notes.append("過去検証: サマリCSV読込不可（前日重みのみ）")
        return

    target_attrs = {
        "style": "style",
        "frame": "frame",
        "market": "market",
        "payout": "payout",
        "bloodline": "bloodline",
        "training": "training",
    }
    adjustments: dict[str, list[float]] = defaultdict(list)
    labels: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if row.get("group") != "予測信号":
            continue
        attr = target_attrs.get(row.get("signal_type", ""))
        if not attr:
            continue
        rate = parse_percent(row.get("reproduced_or_partial_rate", ""))
        if rate is None:
            continue
        signal_type = row.get("signal_type", "")
        multiplier = historical_adjustment_from_rate(rate, row.get("sample_note", ""), signal_type)
        adjustments[attr].append(multiplier)
        labels[attr].append(f"{row.get('signal_label', row.get('signal_type', attr))}{rate * 100:.0f}%")

    for attr, values in adjustments.items():
        current = getattr(weights, attr)
        multiplier = sum(values) / len(values)
        setattr(weights, attr, max(0.75, min(1.30, current * multiplier)))
        weights.notes.append(f"過去検証: {'/'.join(labels[attr])} を{attr}重みに反映（{multiplier:.2f}倍）")


def validation_summary_path_for_target(output_dir: Path, target_year: int) -> Path:
    yearly = output_dir / f"trend_validation_summary_{target_year}.csv"
    return yearly if yearly.exists() else output_dir / "trend_validation_summary.csv"


def apply_historical_mining_weight(conn: sqlite3.Connection, weights: TrendWeights, before_date: str) -> None:
    sql = """
        SELECT
            COUNT(*) AS starters,
            SUM(CASE WHEN CAST(COALESCE(mining_predicted_order,0) AS INTEGER) BETWEEN 1 AND 3 THEN 1 ELSE 0 END) AS dm_top3_rank,
            SUM(CASE WHEN CAST(COALESCE(mining_predicted_order,0) AS INTEGER) BETWEEN 1 AND 3
                       AND (CAST(COALESCE(confirmed_order,0) AS INTEGER) BETWEEN 1 AND 3
                         OR CAST(COALESCE(finish_order,0) AS INTEGER) BETWEEN 1 AND 3)
                     THEN 1 ELSE 0 END) AS dm_top3_hit,
            SUM(CASE WHEN CAST(COALESCE(confirmed_order,0) AS INTEGER) BETWEEN 1 AND 3
                       OR CAST(COALESCE(finish_order,0) AS INTEGER) BETWEEN 1 AND 3
                     THEN 1 ELSE 0 END) AS actual_top3
          FROM horse_races
         WHERE race_year || race_month_day < ?
           AND track_code IN ('01','02','03','04','05','06','07','08','09','10')
           AND TRIM(COALESCE(horse_num, '')) NOT IN ('', '00')
           AND COALESCE(NULLIF(TRIM(abnormal_code), ''), '0') = '0'
           AND CAST(COALESCE(mining_predicted_order,0) AS INTEGER) > 0
           AND (
                CAST(COALESCE(confirmed_order,0) AS INTEGER) > 0
                OR CAST(COALESCE(finish_order,0) AS INTEGER) > 0
           )
    """
    try:
        row = conn.execute(sql, (before_date,)).fetchone()
    except sqlite3.Error:
        weights.notes.append("データマイニング: 過去成績を読込不可")
        return
    if not row:
        weights.notes.append("データマイニング: 過去成績なし")
        return

    starters = int(row["starters"] or 0)
    dm_rank = int(row["dm_top3_rank"] or 0)
    dm_hit = int(row["dm_top3_hit"] or 0)
    actual_top3 = int(row["actual_top3"] or 0)
    if starters < 300 or dm_rank < 50:
        weights.notes.append("データマイニング: 過去サンプル不足")
        return
    overall_rate = actual_top3 / starters if starters else 0.0
    dm_rate = dm_hit / dm_rank if dm_rank else 0.0
    edge = dm_rate - overall_rate
    if edge >= 0.12:
        multiplier = 1.18
    elif edge >= 0.07:
        multiplier = 1.10
    elif edge <= -0.04:
        multiplier = 0.88
    else:
        multiplier = 1.00
    weights.mining = max(0.75, min(1.30, weights.mining * multiplier))
    weights.notes.append(
        f"データマイニング過去: 上位3位の3着内率{pct(dm_hit, dm_rank)}"
        f"（全体{pct(actual_top3, starters)}）を反映"
    )


def intraday_multiplier(edge: float, *, strong: float, weak: float) -> float:
    if edge >= strong:
        return 1.08
    if edge >= strong * 0.6:
        return 1.04
    if edge <= -weak:
        return 0.92
    if edge <= -weak * 0.6:
        return 0.96
    return 1.0


def apply_intraday_result_weights(conn: sqlite3.Connection, weights: TrendWeights, intraday_date: str | None) -> None:
    if not intraday_date:
        return
    try:
        rows = load_result_rows(conn, intraday_date)
        races = build_races(rows)
    except sqlite3.Error:
        weights.notes.append("当日途中結果: 読込不可（補正なし）")
        return

    if len(races) < MIN_INTRADAY_WEIGHT_RACES:
        weights.notes.append(
            f"当日途中結果: {display_date(intraday_date)}は確定{len(races)}Rのため補正なし"
        )
        return

    stats = summarize("当日途中", races)
    starters = all_starters(races)
    if stats.starter_count <= 0 or stats.top3_count <= 0:
        weights.notes.append("当日途中結果: 有効な着順サンプルなし（補正なし）")
        return

    overall_rate = top3_rate(stats.top3_count, stats.starter_count)
    weights.notes.append(
        f"当日途中結果: {display_date(intraday_date)}確定{len(races)}Rを軽量補正に使用"
    )

    mining_top = [h for h in starters if 1 <= h.mining_rank <= 3]
    mining_hit = [h for h in mining_top if 1 <= h.order <= 3]
    if len(mining_top) >= MIN_INTRADAY_SIGNAL_STARTERS:
        rate = top3_rate(len(mining_hit), len(mining_top))
        multiplier = intraday_multiplier(rate - overall_rate, strong=0.08, weak=0.04)
        if multiplier != 1.0:
            weights.mining = max(0.75, min(1.30, weights.mining * multiplier))
            direction = "上方" if multiplier > 1.0 else "下方"
            weights.notes.append(
                f"当日途中結果: データマイニングを{direction}補正"
                f"（DM1-3位 {len(mining_hit)}/{len(mining_top)}頭）"
            )

    frame_edges: list[tuple[str, float, int, int]] = []
    for bucket, starters_count in stats.starter_buckets.items():
        if bucket == "不明" or starters_count <= 0:
            continue
        top3_count = stats.top3_buckets[bucket]
        frame_edges.append((bucket, top3_rate(top3_count, starters_count) - overall_rate, top3_count, starters_count))
    if frame_edges:
        best_bucket, best_edge, best_count, best_starters = max(frame_edges, key=lambda item: item[1])
        multiplier = intraday_multiplier(best_edge, strong=0.08, weak=0.08)
        if multiplier > 1.0 and best_count >= 6:
            weights.frame = max(0.75, min(1.30, weights.frame * multiplier))
            weights.notes.append(
                f"当日途中結果: 枠を上方補正"
                f"（{best_bucket}枠 {best_count}/{best_starters}頭）"
            )
        elif max(edge for _, edge, _, _ in frame_edges) < 0.06:
            weights.frame = max(0.75, min(1.30, weights.frame * 0.96))
            weights.notes.append("当日途中結果: 枠の優位が薄く枠重みを微減")


def disable_mining_weight_if_missing(weights: TrendWeights, horses: list[NextHorse]) -> None:
    if not horses:
        return
    mining_count = sum(1 for horse in horses if horse.mining_rank > 0)
    if mining_count == 0:
        weights.mining = 0.0
        weights.notes.append("データマイニング: 対象出走馬が未取得のため今回スコアでは無効")
    elif mining_count / len(horses) < 0.5:
        weights.mining *= 0.5
        weights.notes.append(f"データマイニング: 取得率{pct(mining_count, len(horses))}のため重み半減")


def adjust_weights_for_next_data(weights: TrendWeights, horses: list[NextHorse]) -> None:
    if not horses:
        return
    style_count = sum(1 for horse in horses if horse.style)
    if style_count == 0:
        weights.style = 0.0
        weights.notes.append("脚質: 対象出走馬の脚質データ未取得のため今回スコアでは無効")
    elif style_count / len(horses) < 0.5:
        weights.style *= 0.5
        weights.notes.append(f"脚質: 取得率{pct(style_count, len(horses))}のため重み半減")


def trend_weight_items(weights: TrendWeights) -> list[tuple[str, float]]:
    # 人気/堅実・荒れ/配当はオッズ・人気に依存する採点だったため、スコア非使用となり一覧から除外。
    return [
        ("枠", weights.frame),
        ("脚質", weights.style),
        ("データマイニング", weights.mining),
        ("調教", weights.training),
        ("血統", weights.bloodline),
        ("騎手同コース", weights.jockey),
    ]


def trend_sample_sufficient(stats: TrendStats) -> bool:
    return len(stats.races) >= MIN_TREND_RACES and stats.starter_count >= MIN_TREND_STARTERS


def summarize_if_sufficient(label: str, races: list[RaceResult]) -> TrendStats | None:
    if not races:
        return None
    stats = summarize(label, races)
    return stats if trend_sample_sufficient(stats) else None


def trend_notes(stats: TrendStats) -> list[str]:
    notes: list[str] = []
    race_count = len(stats.races)
    if race_count == 0:
        return ["対象レースなし"]
    if not trend_sample_sufficient(stats):
        return [f"サンプル{race_count}R/{stats.starter_count}頭のため傾向断定不可"]

    overall_top3_rate = stats.top3_count / stats.starter_count if stats.starter_count else 0.0
    bucket_rates: list[tuple[str, float, int, int]] = []
    for bucket, starters in stats.starter_buckets.items():
        if bucket == "不明" or starters <= 0:
            continue
        top3 = stats.top3_buckets[bucket]
        bucket_rates.append((bucket, top3 / starters, top3, starters))
    if bucket_rates:
        bucket, rate, top3, starters = max(bucket_rates, key=lambda item: (item[1], item[2]))
        if top3 >= 2 and rate >= overall_top3_rate + 0.06:
            notes.append(f"{bucket}枠の馬券内率高め（{top3}/{starters}頭、{pct(top3, starters)}）")

    front = stats.top3_styles["逃げ"] + stats.top3_styles["先行"]
    late = stats.top3_styles["差し"] + stats.top3_styles["追込"]
    front_starters = stats.starter_styles["逃げ"] + stats.starter_styles["先行"]
    late_starters = stats.starter_styles["差し"] + stats.starter_styles["追込"]
    if front_starters or late_starters:
        front_rate = front / front_starters if front_starters else 0.0
        late_rate = late / late_starters if late_starters else 0.0
        if front >= 3 and front_rate >= overall_top3_rate + 0.08:
            notes.append(f"前目脚質の馬券内率高め（逃げ・先行 {front}/{front_starters}頭、{pct(front, front_starters)}）")
        elif late >= 3 and late_rate >= overall_top3_rate + 0.08:
            notes.append(f"差し寄り脚質の馬券内率高め（差し・追込 {late}/{late_starters}頭、{pct(late, late_starters)}）")

    if stats.top3_count:
        fast_rate = stats.fast3f_top3 / stats.top3_count
        if fast_rate >= 0.55:
            notes.append(f"結果確認: 上がり3位内が3着内の{pct(stats.fast3f_top3, stats.top3_count)}")
        elif fast_rate <= 0.30:
            notes.append(f"結果確認: 上がり3位内は3着内の{pct(stats.fast3f_top3, stats.top3_count)}")

    if stats.winner_popularities:
        avg_pop = sum(stats.winner_popularities) / len(stats.winner_popularities)
        if avg_pop <= 3.0:
            notes.append(f"勝ち馬人気は堅め（平均{avg_pop:.1f}人気）")
        elif avg_pop >= 5.0:
            notes.append(f"人気薄の勝ち切りに注意（勝ち馬平均{avg_pop:.1f}人気）")

    if stats.high_payout_races / race_count >= 0.30:
        notes.append(f"荒れ気味（高配当レース {stats.high_payout_races}/{race_count}）")

    return notes or ["強い偏りは薄め。枠・脚質より個別能力と展開を優先"]


def counter_text(
    counter: Counter,
    total: int,
    key_suffix: str = "",
    count_suffix: str = "件",
    include_rate: bool = True,
) -> str:
    if not counter or total <= 0:
        return "-"
    if not include_rate:
        return " / ".join(
            f"{k}{key_suffix}:{v}{count_suffix}"
            for k, v in counter.most_common()
        )
    return " / ".join(
        f"{k}{key_suffix}:{v}{count_suffix}({pct(v, total)})"
        for k, v in counter.most_common()
    )


def ratio_counter_text(
    numerators: Counter,
    denominators: Counter,
    key_suffix: str = "",
    count_suffix: str = "頭",
    include_rate: bool = True,
) -> str:
    items: list[tuple[object, int, int, float]] = []
    for key, den in denominators.items():
        if den <= 0:
            continue
        num = numerators[key]
        items.append((key, num, den, num / den))
    if not items:
        return "-"
    if include_rate:
        items.sort(key=lambda item: (item[3], item[1]), reverse=True)
    else:
        items.sort(key=lambda item: (item[1], item[2]), reverse=True)
        return " / ".join(
            f"{key}{key_suffix}:{num}{count_suffix}"
            for key, num, _den, _rate in items
        )
    return " / ".join(
        f"{key}{key_suffix}:{num}/{den}{count_suffix}({pct(num, den)})"
        for key, num, den, _rate in items
    )


def final3f_result_text(stats: TrendStats, include_rate: bool = True) -> str:
    if include_rate:
        return f"3着内{stats.fast3f_top3}/{stats.top3_count}頭({pct(stats.fast3f_top3, stats.top3_count)})"
    return f"3着内{stats.fast3f_top3}頭（率非表示）"


def avg_int(values: list[int]) -> int:
    return round(sum(values) / len(values)) if values else 0


def signed_kg(value: int | None) -> str:
    if value is None:
        return "-"
    if value > 0:
        return f"+{value}kg"
    return f"{value}kg"


def body_weight_notes(races: list[RaceResult]) -> list[str]:
    starters = all_starters(races)
    weighted = [h for h in starters if h.horse_weight > 0]
    top3 = [h for race in races for h in race.top3 if h.horse_weight > 0]
    if not weighted or not top3:
        return ["馬体重データなし"]

    large_gain = [h for h in weighted if h.weight_change is not None and h.weight_change >= 10]
    large_loss = [h for h in weighted if h.weight_change is not None and h.weight_change <= -10]
    top3_large_gain = [h for h in top3 if h.weight_change is not None and h.weight_change >= 10]
    top3_large_loss = [h for h in top3 if h.weight_change is not None and h.weight_change <= -10]
    top3_changes = [h.weight_change for h in top3 if h.weight_change is not None]
    notes = [
        f"馬体重取得済み {len(weighted)}/{len(starters)}頭、3着内 {len(top3)}頭",
        f"3着内馬の平均馬体重 {avg_int([h.horse_weight for h in top3])}kg（全体平均 {avg_int([h.horse_weight for h in weighted])}kg）",
    ]
    if top3_changes:
        notes.append(f"3着内馬の平均増減 {signed_kg(avg_int(top3_changes))}")
    notes.append(
        "大幅増減: "
        f"+10kg以上 {len(top3_large_gain)}/{len(large_gain)}頭が3着内、"
        f"-10kg以下 {len(top3_large_loss)}/{len(large_loss)}頭が3着内"
    )
    return notes


def body_weight_groups(
    by_track_surface: dict[tuple[str, str], list[RaceResult]],
) -> list[tuple[str, list[str]]]:
    """馬体重の最適帯・増減耐性は競馬場×馬場で異なるため、一括ではなく
    競馬場×馬場ごとに集計する。データのない区分は表示しない。"""
    groups: list[tuple[str, list[str]]] = []
    for (track_code, surface), group in sorted(by_track_surface.items()):
        notes = body_weight_notes(group)
        if notes == ["馬体重データなし"]:
            continue
        label = f"{TRACK_NAMES.get(track_code, track_code)} {surface}"
        groups.append((label, notes))
    return groups


def jockey_course_text(stats: JockeyCourseStats | None) -> str:
    if not stats or stats.starts <= 0:
        return "騎手同コース成績なし"
    return (
        f"騎手同コース {stats.starts}走 "
        f"{stats.wins}勝 3着内{stats.top3}回({pct(stats.top3, stats.starts)})"
    )


def pick_mark(index: int) -> str:
    marks = ["◎", "○", "▲"]
    return marks[index] if index < len(marks) else "・"


def e(value: object) -> str:
    return html.escape(str(value), quote=True)


def is_result_check_note(note: str) -> bool:
    return note.startswith("結果確認")


def split_trend_notes(notes: list[str]) -> tuple[list[str], list[str]]:
    predictive: list[str] = []
    result_check: list[str] = []
    for note in notes:
        if is_result_check_note(note):
            result_check.append(note)
        else:
            predictive.append(note)
    return predictive, result_check


def predictive_trend_notes(stats: TrendStats) -> list[str]:
    predictive, _result_check = split_trend_notes(trend_notes(stats))
    return predictive


def carryover_confidence(note: str) -> str:
    if is_result_check_note(note):
        return "対象外"
    if "サンプル" in note or "対象レースなし" in note or "強い偏りは薄め" in note:
        return "対象外"
    if "枠" in note:
        return "低"
    if "前目脚質" in note or "差し寄り脚質" in note:
        return "中"
    if "荒れ気味" in note or "勝ち馬人気" in note or "人気薄" in note:
        return "中"
    return "低"


def bloodline_carryover_confidence(note: str) -> str:
    if "サンプル不足" in note or "データなし" in note:
        return "対象外"
    return "低"


def confidence_prefix(confidence: str) -> str:
    if confidence == "対象外":
        return "持ち越し対象外"
    return f"持ち越し信頼度: {confidence}"


def score_factor(confidence: str) -> float:
    return SCORE_CONFIDENCE_FACTORS.get(confidence, SCORE_CONFIDENCE_FACTORS["低"])


def score_reason_suffix(confidence: str) -> str:
    if score_factor(confidence) >= 1:
        return ""
    if confidence == "対象外":
        return "（持ち越し対象外）"
    return "（低信頼・加点抑制）"


def format_carryover_note(note: str, confidence: str | None = None) -> str:
    confidence = confidence or carryover_confidence(note)
    return f"[{confidence_prefix(confidence)}] {note}"


def compact_note_text(notes: list[str], limit: int = 2, include_confidence: bool = True) -> str:
    if not notes:
        return "-"
    display_notes = notes[:limit]
    if include_confidence:
        display_notes = [format_carryover_note(note) for note in display_notes]
    return " / ".join(display_notes)


def append_markdown_note_groups(lines: list[str], notes: list[str]) -> None:
    predictive, result_check = split_trend_notes(notes)
    lines.append("**予測利用可**")
    if predictive:
        for note in predictive:
            lines.append(f"- {format_carryover_note(note)}")
    else:
        lines.append("- 該当なし")
    lines.append("")
    lines.append("**結果確認**")
    if result_check:
        for note in result_check:
            lines.append(f"- {format_carryover_note(note)}")
    else:
        lines.append("- 該当なし")


def append_html_note_groups(parts: list[str], notes: list[str]) -> None:
    predictive, result_check = split_trend_notes(notes)
    groups = [
        ("予測利用可", predictive, "pred"),
        ("結果確認", result_check, "result"),
    ]
    for label, group_notes, kind in groups:
        parts.append(f'<div class="usage-label {kind}">{e(label)}</div>')
        parts.append('<ul class="notes">')
        if group_notes:
            for note in group_notes:
                parts.append(f"<li>{e(format_carryover_note(note))}</li>")
        else:
            parts.append("<li>該当なし</li>")
        parts.append("</ul>")


def append_html_track_trend_overview(
    parts: list[str],
    track_surface_stats: list[tuple[str, TrendStats]],
) -> None:
    parts.append('<article class="card quick-card">')
    parts.append("<h3>傾向早見</h3>")
    parts.append('<div class="table-wrap">')
    parts.append(
        '<table class="table trend-table"><thead><tr>'
        "<th>場/馬場</th><th>R</th><th>予測に使う要点</th><th>結果確認</th>"
        "</tr></thead><tbody>"
    )
    for label, stats in track_surface_stats:
        notes = trend_notes(stats)
        predictive, result_check = split_trend_notes(notes)
        parts.append(
            f"<tr><td><strong>{e(label)}</strong></td><td>{len(stats.races)}</td>"
            f"<td>{e(compact_note_text(predictive, limit=2, include_confidence=False))}</td>"
            f"<td>{e(compact_note_text(result_check, limit=1, include_confidence=False))}</td></tr>"
        )
    parts.append("</tbody></table></div></article>")


def append_html_weight_card(parts: list[str], weights: TrendWeights) -> None:
    parts.append('<article class="card quick-card">')
    parts.append("<h3>今回の重みづけ</h3>")
    parts.append('<div class="dashboard-grid weight-grid">')
    for label, value in trend_weight_items(weights):
        applied = weights.applied_counts.get(label, 0)
        applied_text = f" / 適用{applied}件" if weights.applied_counts else ""
        parts.append(
            f'<div class="metric"><b>{e(label)}</b>'
            f'<span>{e(weight_label(value))}</span><small>{e(weight_multiplier_text(value) + applied_text)}</small></div>'
        )
    parts.append("</div>")
    if weights.notes:
        parts.append('<ul class="notes">')
        for note in weights.notes[:12]:
            parts.append(f"<li>{e(note)}</li>")
        parts.append("</ul>")
    parts.append("</article>")


def append_html_body_weight_card(
    parts: list[str],
    groups: list[tuple[str, list[str]]],
    overall_notes: list[str],
) -> None:
    parts.append('<article class="card">')
    parts.append("<h3>馬体重傾向</h3>")
    parts.append('<p class="sub">区分: 結果確認 / おすすめスコアには未使用。馬体重の最適帯・増減耐性は競馬場×馬場で異なるため分けて表示します。当日発表後に確認する材料です。</p>')
    if groups:
        parts.append('<div class="grid cols">')
        for label, notes in groups:
            parts.append('<article class="card">')
            parts.append(f"<h4>{e(label)}</h4>")
            parts.append('<ul class="notes">')
            for note in notes:
                parts.append(f"<li>{e(note)}</li>")
            parts.append("</ul></article>")
        parts.append("</div>")
    else:
        parts.append('<ul class="notes"><li>馬体重データなし</li></ul>')
    parts.append('<details class="race-group">')
    parts.append("<summary>馬体重全体（参考）を見る</summary>")
    parts.append('<div class="race-group-body"><ul class="notes">')
    for note in overall_notes:
        parts.append(f"<li>{e(note)}</li>")
    parts.append("</ul></div></details>")
    parts.append("</article>")


def append_html_score_band_note(parts: list[str], score_bands: list["ScoreBandStat"]) -> None:
    total_bets = sum(band.bet_count for band in score_bands)
    if total_bets < SCORE_BAND_MIN_TOTAL:
        return
    parts.append('<article class="card">')
    parts.append("<h3>過去のスコア帯別実績</h3>")
    parts.append(
        f'<p class="sub">評価済みの馬券対象{total_bets}件の後付け集計です。'
        "スコアが高い帯ほど複勝率が上がる傾向を確認できます。的中や利益を保証するものではありません。</p>"
    )
    parts.append('<div class="table-wrap">')
    parts.append(
        '<table class="table band-table"><thead><tr>'
        '<th>スコア帯</th><th class="num">件数</th><th class="num">複勝率</th><th class="num">複勝回収</th>'
        "</tr></thead><tbody>"
    )
    has_reference = False
    for band in score_bands:
        mark = "*" if band.is_reference else ""
        has_reference = has_reference or band.is_reference
        parts.append(
            f"<tr><td><strong>{e(band.label)}{mark}</strong></td>"
            f'<td class="num">{band.bet_count}</td>'
            f'<td class="num">{e(band.top3_rate)}</td>'
            f'<td class="num">{e(band.place_return_rate)}</td></tr>'
        )
    parts.append("</tbody></table></div>")
    footnote = "複勝回収は各推奨を複勝100円で買った場合の単純集計です。"
    if has_reference:
        footnote += f"（* は件数{SCORE_BAND_REFERENCE_SAMPLE}件未満の参考値）"
    parts.append(f'<p class="small">{footnote}</p>')
    parts.append("</article>")


def append_html_race_overview_row(parts: list[str], summary: NextRaceSummary) -> None:
    pick_text = recommendation_summary(summary.recommendations)
    if not pick_text and summary.reference_recommendations:
        pick_text = "参考 " + recommendation_summary(summary.reference_recommendations)
    pick_text = pick_text or "推奨なし"
    if summary.recommendations:
        row_class = "race-row has-pick"
    elif summary.reference_recommendations:
        row_class = "race-row has-reference"
    else:
        row_class = "race-row no-pick"
    race_text = race_label(summary.track, summary.race_num, summary.race_name)
    if summary.recommendations or summary.reference_recommendations:
        race_cell = f'<a class="race-link" href="#{e(summary.race_id)}">{e(race_text)}</a>'
    else:
        race_cell = f'<span class="race-link muted">{e(race_text)}</span>'
    parts.append(f'<div class="{row_class}">')
    parts.append(f'<div class="race-cell main">{race_cell}</div>')
    parts.append(f'<div class="race-cell time">{e(summary.start_time) or "-"}</div>')
    parts.append(f'<div class="race-cell condition">{e(summary.condition)}</div>')
    parts.append(f'<div class="race-cell pick">{e(pick_text)}</div>')
    parts.append("</div>")


def append_html_race_overview(parts: list[str], summaries: list[NextRaceSummary]) -> None:
    picked = [s for s in summaries if s.recommendations or s.reference_recommendations]
    no_pick = [s for s in summaries if not (s.recommendations or s.reference_recommendations)]
    parts.append('<article class="card race-overview-card">')
    parts.append("<h3>対象レース早見</h3>")
    if picked:
        parts.append('<div class="race-overview">')
        for summary in picked:
            append_html_race_overview_row(parts, summary)
        parts.append("</div>")
    elif no_pick:
        parts.append('<p class="sub">推奨・参考候補のあるレースはありません。</p>')
    if no_pick:
        parts.append('<details class="race-group">')
        parts.append(f"<summary>推奨なしのレース（{len(no_pick)}R）を見る</summary>")
        parts.append('<div class="race-group-body"><div class="race-overview">')
        for summary in no_pick:
            append_html_race_overview_row(parts, summary)
        parts.append("</div></div></details>")
    parts.append("</article>")


def append_html_recommendation_detail(
    parts: list[str],
    summary: NextRaceSummary,
    *,
    reference: bool = False,
    open_detail: bool = False,
) -> None:
    candidates = summary.reference_recommendations if reference else summary.recommendations
    summary_picks = recommendation_summary(candidates) or ("参考候補なし" if reference else "推奨馬なし")
    detail_class = "race reference" if reference else "race recommended"
    detail_id = f"{summary.race_id}-ref" if reference and summary.recommendations else summary.race_id
    open_attr = " open" if open_detail else ""
    parts.append(f'<details id="{e(detail_id)}" class="{detail_class}"{open_attr}>')
    parts.append("<summary>")
    parts.append('<div class="summary-main">')
    full_condition = f"{summary.start_time} {summary.condition}".strip()
    parts.append(
        f"<div><strong>{e(race_label(summary.track, summary.race_num, summary.race_name))}</strong>"
        f'<div class="summary-condition">{e(full_condition)}</div>'
        f'<div class="summary-picks">{e(summary_picks)}</div></div>'
    )
    parts.append('<span class="pill">詳細</span>')
    parts.append("</div>")
    parts.append("</summary>")
    parts.append('<div class="detail-body">')
    for idx, rec in enumerate(candidates):
        h = rec.horse
        parts.append('<div class="pick-mini">')
        parts.append('<div class="race-title">')
        parts.append(
            f'<div><div class="horse">{e(pick_mark(idx))} {e(h.horse_num)} {e(h.name)}</div>'
            f'<div class="sub">{e(h.condition)} / 傾向: {e(rec.trend_source)}</div></div>'
        )
        parts.append("</div>")
        parts.append('<div class="chips">')
        parts.append(
            f'<span class="chip strong">評価 {rec.score}点</span><span class="chip">{h.frame}枠</span>'
            f'<span class="chip">{e(h.jockey) or "騎手未定"}</span>'
        )
        parts.append(f'<span class="chip wide">{e(jockey_course_text(h.jockey_course))}</span>')
        if h.style:
            parts.append(f'<span class="chip">{e(h.style)}</span>')
        if h.mining_rank:
            parts.append(f'<span class="chip">DM {h.mining_rank}位</span>')
        if h.sire_name:
            parts.append(f'<span class="chip wide">父 {e(h.sire_name)}</span>')
        if h.dam_sire_name:
            parts.append(f'<span class="chip wide">母父 {e(h.dam_sire_name)}</span>')
        parts.append("</div>")
        if h.training:
            parts.append('<div class="tag-row">')
            parts.append('<span class="tag muted">調教</span>')
            for tag in training_pattern_tags(h.training):
                parts.append(f'<span class="tag">{e(tag)}</span>')
            parts.append("</div>")
            parts.append('<details class="raw-training">')
            parts.append("<summary>調教時計</summary>")
            parts.append(
                f'<div class="raw-body">{e(training_course_text(h.training))} '
                f'{e(display_date(str(h.training.get("training_date") or "")))}<br>'
                f'時計: {e(training_work_summary(h.training))}<br>'
                f'ラップ: {e(training_lap_text(h.training))}</div>'
            )
            parts.append("</details>")
        parts.append('<ul class="reason">')
        for reason in rec.reasons:
            parts.append(f"<li>{e(reason)}</li>")
        parts.append("</ul>")
        parts.append("</div>")
    parts.append("</div></details>")


def trend_scope_label(surface: str, band: str) -> str:
    return surface if surface == band else f"{surface}{band}"


def choose_trend_stats(
    horse: NextHorse,
    by_track: dict[str, list[RaceResult]],
    by_track_surface: dict[tuple[str, str], list[RaceResult]],
    by_track_surface_band: dict[tuple[str, str, str], list[RaceResult]],
) -> tuple[TrendStats | None, str]:
    band_group = by_track_surface_band.get((horse.track_code, horse.surface, horse.band), [])
    surface_group = by_track_surface.get((horse.track_code, horse.surface), [])
    band_stats = summarize_if_sufficient(f"{horse.track} {horse.surface} {horse.band}", band_group)
    if band_stats:
        return band_stats, trend_scope_label(horse.surface, horse.band)
    surface_stats = summarize_if_sufficient(f"{horse.track} {horse.surface}", surface_group)
    if surface_stats:
        return surface_stats, f"{horse.surface}全体"
    return None, "サンプル不足" if band_group or surface_group else "-"


def choose_bloodline_stats(
    horse: NextHorse,
    by_track: dict[str, list[RaceResult]],
    by_track_surface: dict[tuple[str, str], list[RaceResult]],
    by_track_surface_band: dict[tuple[str, str, str], list[RaceResult]],
) -> BloodlineStats | None:
    band_group = by_track_surface_band.get((horse.track_code, horse.surface, horse.band), [])
    surface_group = by_track_surface.get((horse.track_code, horse.surface), [])
    if summarize_if_sufficient(f"{horse.track} {horse.surface} {horse.band}", band_group):
        return summarize_bloodlines(band_group)
    if summarize_if_sufficient(f"{horse.track} {horse.surface}", surface_group):
        return summarize_bloodlines(surface_group)
    return None


def score_horse(
    horse: NextHorse,
    stats: TrendStats | None,
    source: str,
    bloodline_stats: BloodlineStats | None = None,
    weights: TrendWeights | None = None,
) -> Recommendation:
    weights = weights or TrendWeights()
    if not stats or stats.top3_count <= 0:
        return Recommendation(horse=horse, score=0, trend_source=source, reasons=["当日傾向サンプルなし"])
    if not trend_sample_sufficient(stats):
        return Recommendation(horse=horse, score=0, trend_source=source, reasons=["当日傾向サンプル不足"])

    score = 40.0
    reasons: list[str] = []
    bucket = frame_bucket(horse.frame)
    overall_top3_rate = stats.top3_count / stats.starter_count if stats.starter_count else 0.0

    bucket_starters = stats.starter_buckets[bucket]
    bucket_top3 = stats.top3_buckets[bucket]
    bucket_rate = bucket_top3 / bucket_starters if bucket_starters else 0.0
    if bucket_top3 >= 2 and bucket_rate >= overall_top3_rate + 0.06:
        add = min(9, (bucket_rate - overall_top3_rate) * 45)
        confidence = "低"
        score += add * score_factor(confidence) * weights.frame
        reasons.append(f"{bucket}枠の馬券内率{pct(bucket_top3, bucket_starters)}{score_reason_suffix(confidence)}")

    frame_wins = stats.winner_frames[horse.frame]
    frame_share = frame_wins / len(stats.races) if frame_wins else 0.0
    if frame_wins >= 2:
        add = min(6, frame_share * 10)
        confidence = "低"
        score += add * score_factor(confidence) * weights.frame
        reasons.append(f"{horse.frame}枠が当日{frame_wins}勝{score_reason_suffix(confidence)}")

    style_starters = stats.starter_styles[horse.style] if horse.style else 0
    style_top3 = stats.top3_styles[horse.style] if horse.style else 0
    style_rate = style_top3 / style_starters if style_starters else 0.0
    if horse.style and style_top3 >= 3 and style_rate >= overall_top3_rate + 0.07:
        score += min(12, (style_rate - overall_top3_rate) * 50) * weights.style
        reasons.append(f"{horse.style}脚質の馬券内率{pct(style_top3, style_starters)}")

    # オッズ・人気（前日の市場データ）は採点に使わない。傾向レポートは出馬表段階で
    # 分かる特徴だけで評価する方針のため、人気・オッズと、それに紐づく配当加点は外す。
    if horse.mining_rank:
        if horse.mining_rank == 1:
            score += 15 * weights.mining
            reasons.append("データマイニング1位")
        elif horse.mining_rank <= 3:
            score += 10 * weights.mining
            reasons.append(f"データマイニング{horse.mining_rank}位")
        elif horse.mining_rank <= 5:
            score += 5 * weights.mining
            reasons.append(f"データマイニング{horse.mining_rank}位")

    if horse.training:
        training_tags = training_pattern_tags(horse.training)
        training_add = 0
        negative_reasons: list[str] = []
        if _training_fast_finish(horse.training):
            training_add += 8
        elif _training_good_finish(horse.training):
            training_add += 5
        if _training_accelerated(horse.training):
            training_add += 5
        if _training_fast_total(horse.training):
            training_add += 4
        if _training_long_work(horse.training):
            training_add += 2
        if _training_light_finish_focus(horse.training):
            training_add += 2
        if _training_slowed(horse.training):
            score -= 3 * weights.training
            negative_reasons.append("減点: 調教終い失速")
        score += min(10, training_add) * INDIVIDUAL_TRAINING_SCORE_FACTOR * weights.training
        positive_tags = [tag for tag in training_tags if tag not in {"標準的", "終い失速"}]
        if positive_tags:
            reasons.append("調教: " + "・".join(positive_tags[:3]) + "（加点抑制）")
        reasons.extend(negative_reasons)

    if bloodline_stats and bloodline_stats.top3_count:
        bloodline_add = 0
        bloodline_overall = bloodline_stats.top3_count / bloodline_stats.starter_count if bloodline_stats.starter_count else 0.0
        if horse.sire_name and horse.sire_name in bloodline_stats.sire_top3:
            count = bloodline_stats.sire_top3[horse.sire_name]
            starters = bloodline_stats.sire_starters[horse.sire_name]
            rate = count / starters if starters else 0.0
            if (
                starters >= MIN_BLOODLINE_STARTERS
                and count >= MIN_BLOODLINE_TOP3
                and rate >= bloodline_overall + MIN_BLOODLINE_RATE_EDGE
            ):
                bloodline_add += min(8, 2 + (rate - bloodline_overall) * 20)
                reasons.append(f"父{horse.sire_name}の馬券内率{pct(count, starters)}{score_reason_suffix('低')}")
        if horse.dam_sire_name and horse.dam_sire_name in bloodline_stats.dam_sire_top3:
            count = bloodline_stats.dam_sire_top3[horse.dam_sire_name]
            starters = bloodline_stats.dam_sire_starters[horse.dam_sire_name]
            rate = count / starters if starters else 0.0
            if (
                starters >= MIN_BLOODLINE_STARTERS
                and count >= MIN_BLOODLINE_TOP3
                and rate >= bloodline_overall + MIN_BLOODLINE_RATE_EDGE
            ):
                bloodline_add += min(5, 1 + (rate - bloodline_overall) * 12)
                reasons.append(f"母父{horse.dam_sire_name}の馬券内率{pct(count, starters)}{score_reason_suffix('低')}")
        score += min(10, bloodline_add) * score_factor("低") * weights.bloodline

    js = horse.jockey_course
    if js and js.starts:
        if js.starts >= 5:
            if js.top3_rate >= 0.35:
                score += 12 * weights.jockey
                reasons.append(f"騎手同コース3着内{pct(js.top3, js.starts)}（{js.starts}走）")
            elif js.win_rate >= 0.15:
                score += 8 * weights.jockey
                reasons.append(f"騎手同コース勝率{pct(js.wins, js.starts)}（{js.starts}走）")
            else:
                score += 3 * weights.jockey
                reasons.append(f"騎手同コース経験{js.starts}走")

    if not reasons:
        reasons.append("強い一致なし")
    return Recommendation(horse=horse, score=max(0, min(100, round(score))), trend_source=source, reasons=reasons[:5])


def recommendation_reason_counts(recommendations: list[Recommendation]) -> dict[str, int]:
    counts: Counter = Counter()
    for rec in recommendations:
        text = " / ".join(rec.reasons)
        if "枠" in text:
            counts["枠"] += 1
        if "脚質" in text:
            counts["脚質"] += 1
        if "堅め傾向" in text or "人気面" in text:
            counts["人気/堅実"] += 1
        if "荒れ気味" in text:
            counts["荒れ/配当"] += 1
        if "データマイニング" in text:
            counts["データマイニング"] += 1
        if "調教:" in text or "調教終い失速" in text:
            counts["調教"] += 1
        if "父" in text or "母父" in text:
            counts["血統"] += 1
        if "騎手同コース" in text:
            counts["騎手同コース"] += 1
    return dict(counts)


def build_recommendations(
    next_horses: list[NextHorse],
    by_track: dict[str, list[RaceResult]],
    by_track_surface: dict[tuple[str, str], list[RaceResult]],
    by_track_surface_band: dict[tuple[str, str, str], list[RaceResult]],
    weights: TrendWeights | None = None,
    min_score: int = RECOMMENDATION_MIN_SCORE,
    max_score: int | None = None,
) -> list[Recommendation]:
    recommendations: list[Recommendation] = []
    for horse in next_horses:
        stats, source = choose_trend_stats(horse, by_track, by_track_surface, by_track_surface_band)
        bloodline_stats = choose_bloodline_stats(horse, by_track, by_track_surface, by_track_surface_band)
        rec = score_horse(horse, stats, source, bloodline_stats, weights)
        if rec.score >= min_score and (max_score is None or rec.score < max_score):
            recommendations.append(rec)
    recommendations.sort(
        key=lambda r: (
            r.horse.track_code,
            r.horse.race_num,
            -r.score,
            r.horse.popularity or 99,
            r.horse.horse_num,
        )
    )
    per_race_count: Counter = Counter()
    picked: list[Recommendation] = []
    for rec in recommendations:
        race_key = (rec.horse.track_code, rec.horse.race_num)
        if per_race_count[race_key] >= 3:
            continue
        per_race_count[race_key] += 1
        picked.append(rec)
    picked.sort(key=lambda r: (r.horse.track_code, r.horse.race_num, -r.score))
    return picked


def next_pick_zero_reason(
    next_date: str | None,
    next_rows: list[sqlite3.Row],
    next_horses: list[NextHorse],
    recommendations: list[Recommendation],
    pick_notice: str | None = None,
) -> str | None:
    if recommendations:
        return None
    if pick_notice:
        return pick_notice
    if not next_date:
        return "実行日以降の開催日がDBにありません。出馬表更新後に再実行してください。"
    if not next_rows:
        return f"{display_date(next_date)} のレース番組がDBにありません。出馬表更新後に再実行してください。"
    if not next_horses:
        return f"{display_date(next_date)} の出走馬がDBにありません。出走馬データ更新後に再実行してください。"

    return f"評価{RECOMMENDATION_MIN_SCORE}点以上の馬がありません。条件を満たす推奨馬なしとして扱います。"


def next_pick_data_status(
    next_date: str | None,
    next_rows: list[sqlite3.Row],
    next_horses: list[NextHorse] | None,
    recommendations: list[Recommendation] | None,
    pick_notice: str | None = None,
) -> NextPickDataStatus:
    horses = next_horses or []
    picks = recommendations or []
    training_count = sum(1 for horse in horses if horse.training)
    mining_count = sum(1 for horse in horses if horse.mining_rank > 0)
    recommendation_race_count = len({(rec.horse.track_code, rec.horse.race_num) for rec in picks})
    warnings: list[str] = []

    if pick_notice and picks:
        warnings.append(pick_notice)
    if next_rows and not horses:
        warnings.append("レース番組はありますが、出走馬が取得できていません。")
    if horses and training_count == 0:
        warnings.append("追い切りが未取得です。推奨自体は作成できますが、個別追い切り評価は入りません。")
    if horses and mining_count == 0:
        warnings.append("データマイニング順位が未取得です。DM評価は今回のスコアから外しています。")
    elif horses and mining_count / len(horses) < 0.5:
        warnings.append(f"データマイニング順位の取得率が低いです（{status_count_rate(mining_count, len(horses))}）。")

    return NextPickDataStatus(
        next_date=next_date,
        race_count=len(next_rows),
        horse_count=len(horses),
        training_count=training_count,
        mining_count=mining_count,
        recommendation_count=len(picks),
        recommendation_race_count=recommendation_race_count,
        zero_reason=next_pick_zero_reason(next_date, next_rows, horses, picks, pick_notice),
        warnings=warnings,
    )


def status_count_rate(count: int, total: int) -> str:
    if total <= 0:
        return "0/0頭"
    return f"{count}/{total}頭({pct(count, total)})"


def next_pick_status_items(status: NextPickDataStatus) -> list[tuple[str, str]]:
    target = display_date(status.next_date) if status.next_date else "未検出"
    return [
        ("対象日", target),
        ("出馬表", f"{status.race_count}R / {status.horse_count}頭"),
        ("追い切り", status_count_rate(status.training_count, status.horse_count)),
        ("DM予想", status_count_rate(status.mining_count, status.horse_count)),
        ("推奨馬", f"{status.recommendation_count}頭 / {status.recommendation_race_count}R"),
    ]


def overview_status_items(date_key: str, race_count: int, status: NextPickDataStatus) -> list[tuple[str, str]]:
    return [
        ("集計日", display_date(date_key)),
        ("対象R", f"{race_count}R"),
        ("推奨馬", f"{status.recommendation_count}頭 / {status.recommendation_race_count}R"),
        ("出馬表", f"{status.race_count}R"),
        ("出走馬", f"{status.horse_count}頭"),
        ("追い切り", status_count_rate(status.training_count, status.horse_count)),
        ("DM予想", status_count_rate(status.mining_count, status.horse_count)),
    ]


def overview_decision_text(status: NextPickDataStatus) -> str:
    if status.recommendation_count:
        return (
            f"推奨馬{status.recommendation_count}頭（{status.recommendation_race_count}R）。"
            "レース別おすすめで、推奨ありの詳細を確認できます。"
        )
    if status.zero_reason:
        return f"推奨0件。{status.zero_reason}"
    return "推奨作成の前提データを確認してください。"


def row_value(row: sqlite3.Row | dict, key: str, default: object = "") -> object:
    try:
        value = row[key]
    except (IndexError, KeyError, TypeError):
        return default
    return default if value is None else value


def row_int(row: sqlite3.Row | dict, key: str, default: int = 0) -> int:
    try:
        return int(row_value(row, key, default) or default)
    except (TypeError, ValueError):
        return default


def weight_change_kg(sign: object, diff: object) -> int | None:
    diff_text = str(diff or "").strip()
    if diff_text == "":
        return None
    try:
        value = int(diff_text)
    except ValueError:
        return None
    sign_text = str(sign or "").strip()
    if sign_text == "-":
        return -value
    return value


def recommendation_summary(recommendations: list[Recommendation], limit: int = 3) -> str:
    return " / ".join(
        f"{pick_mark(i)}{rec.horse.horse_num} {rec.horse.name}({rec.score}点)"
        for i, rec in enumerate(recommendations[:limit])
    )


def build_next_race_summaries(
    next_rows: list[sqlite3.Row | dict],
    rec_by_race: dict[tuple[str, int], list[Recommendation]],
    ref_by_race: dict[tuple[str, int], list[Recommendation]] | None = None,
) -> list[NextRaceSummary]:
    ref_by_race = ref_by_race or {}
    summaries: list[NextRaceSummary] = []
    for row in next_rows:
        track_code = str(row_value(row, "track_code")).strip()
        race_num = row_int(row, "race_num")
        surface = surface_from_code(str(row_value(row, "track_type_code")))
        distance = row_int(row, "distance")
        start_time = time_hhmm(str(row_value(row, "start_time")))
        race_name = str(row_value(row, "race_name") or row_value(row, "race_short10")).strip()
        track = TRACK_NAMES.get(track_code, track_code)
        condition = f"{surface}{distance}m" if distance else surface
        recs = sorted(rec_by_race.get((track_code, race_num), []), key=lambda r: -r.score)
        refs = sorted(ref_by_race.get((track_code, race_num), []), key=lambda r: -r.score)
        summaries.append(
            NextRaceSummary(
                track_code=track_code,
                race_num=race_num,
                track=track,
                race_name=race_name,
                start_time=start_time,
                condition=condition,
                race_id=f"race-{track_code}-{race_num:02d}",
                recommendations=recs,
                reference_recommendations=refs,
            )
        )
    return summaries


def next_pick_status_lines(status: NextPickDataStatus) -> list[str]:
    lines = [f"{label}: {value}" for label, value in next_pick_status_items(status)]
    if status.zero_reason:
        lines.append(f"推奨0件の理由: {status.zero_reason}")
    for warning in status.warnings:
        lines.append(f"注意: {warning}")
    return lines


def build_markdown(
    date_key: str,
    db_path: Path,
    races: list[RaceResult],
    next_date: str | None,
    next_rows: list[sqlite3.Row],
    training_notes: list[str] | None = None,
    training_by_race: dict[tuple[str, int], list[dict]] | None = None,
    notice: str | None = None,
    next_horses: list[NextHorse] | None = None,
    recommendations: list[Recommendation] | None = None,
    reference_recommendations: list[Recommendation] | None = None,
    trend_weights: TrendWeights | None = None,
    pick_notice: str | None = None,
) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    by_track_surface: dict[tuple[str, str], list[RaceResult]] = defaultdict(list)
    by_track_surface_band: dict[tuple[str, str, str], list[RaceResult]] = defaultdict(list)
    for race in races:
        by_track_surface[(race.track_code, race.surface)].append(race)
        by_track_surface_band[(race.track_code, race.surface, race.band)].append(race)

    blood_stats = summarize_bloodlines(races)
    race_lookup = {(race.track_code, race.race_num): race for race in races}
    trend_weights = trend_weights or determine_trend_weights(races)
    lines: list[str] = []
    lines.append(f"# 中央競馬 傾向レポート {display_date(date_key)}")
    lines.append("")
    lines.append(f"- 生成日時: {generated_at}")
    lines.append(f"- 元DB: `{db_path}`")
    lines.append(f"- 対象: {len(races)}レース")
    lines.append("")
    if notice:
        lines.append("## 注意")
        lines.append("")
        for part in notice.splitlines():
            lines.append(f"- {part}")
    lines.append("## 場別・馬場別")
    lines.append("")
    lines.append("### 今回の重みづけ")
    lines.append("")
    lines.append("|要素|重み|係数|今回適用|")
    lines.append("|---|---|---:|---:|")
    for label, value in trend_weight_items(trend_weights):
        applied = trend_weights.applied_counts.get(label)
        applied_text = "-" if applied is None else f"{applied}件"
        lines.append(f"|{label}|{weight_label(value)}|{weight_multiplier_text(value)}|{applied_text}|")
    lines.append("")
    for note in trend_weights.notes:
        lines.append(f"- {note}")
    lines.append("")
    for (track_code, surface), group in sorted(by_track_surface.items()):
        stats = summarize(f"{TRACK_NAMES.get(track_code, track_code)} {surface}", group)
        lines.append(f"### {stats.label}")
        lines.append("")
        append_markdown_note_groups(lines, trend_notes(stats))
        lines.append("")
        sample_ok = trend_sample_sufficient(stats)
        bucket_label = "枠帯別 馬券内率" if sample_ok else "枠帯別 3着内数"
        style_label = "脚質別 馬券内率" if sample_ok else "脚質別 3着内数"
        lines.append("|項目|内容|")
        lines.append("|---|---|")
        lines.append(f"|レース数|{len(group)}|")
        lines.append(f"|{bucket_label}|{ratio_counter_text(stats.top3_buckets, stats.starter_buckets, include_rate=sample_ok)}|")
        lines.append(f"|勝ち馬 枠|{counter_text(stats.winner_frames, len(group), key_suffix='枠', count_suffix='勝', include_rate=sample_ok)}|")
        lines.append(f"|{style_label}|{ratio_counter_text(stats.top3_styles, stats.starter_styles, include_rate=sample_ok)}|")
        lines.append(f"|結果確認 上がり3位内|{final3f_result_text(stats, include_rate=sample_ok)}|")
        if not sample_ok:
            lines.append("|率表示|サンプル不足のため非表示|")
        if stats.winner_popularities:
            avg_pop = sum(stats.winner_popularities) / len(stats.winner_popularities)
            lines.append(f"|勝ち馬平均人気|{avg_pop:.1f}人気|")
        lines.append("")

        bands = sorted(
            ((key[2], value) for key, value in by_track_surface_band.items() if key[:2] == (track_code, surface)),
            key=lambda item: ["短距離", "マイル", "中距離", "長距離", "障害"].index(item[0])
            if item[0] in ["短距離", "マイル", "中距離", "長距離", "障害"]
            else 99,
        )
        if bands:
            lines.append("|距離帯|R|予測利用可|結果確認|")
            lines.append("|---|---:|---|---|")
            for band, band_races in bands:
                band_stats = summarize(f"{stats.label} {band}", band_races)
                predictive, result_check = split_trend_notes(trend_notes(band_stats))
                lines.append(
                    f"|{band}|{len(band_races)}|"
                    f"{compact_note_text(predictive)}|{compact_note_text(result_check)}|"
                )
            lines.append("")

    lines.append("## 血統傾向")
    lines.append("")
    lines.append("- 区分: 予測利用可 / 持ち越し信頼度: 低（血統は出馬表段階で確認可能。ただし表示条件を満たすサンプルのみ）")
    for note in bloodline_notes(blood_stats):
        lines.append(f"- {format_carryover_note(note, bloodline_carryover_confidence(note))}")
    lines.append("")
    for label, notes in bloodline_groups(by_track_surface):
        lines.append(f"### {label}")
        for note in notes:
            lines.append(f"- {format_carryover_note(note, bloodline_carryover_confidence(note))}")
        lines.append("")

    lines.append("## 最終追い切り傾向")
    lines.append("")
    lines.append("- 区分: 結果確認 / 持ち越し対象外（当日3着内馬ベースの後付け集計。翌日予測へ直接使う材料ではありません）")
    for note in training_notes or ["追い切りデータなし"]:
        lines.append(f"- {note}")
    lines.append("")
    if training_by_race:
        lines.append("- レース別欄は3着内馬だけの後付け確認です。出走全馬平均との差分ではありません。")
        lines.append("")
        lines.append("|場|R|条件|馬券内馬の要約|馬券内馬の型|")
        lines.append("|---|---:|---|---|---|")
        for key, rows in sorted(training_by_race.items(), key=lambda item: (item[0][0], item[0][1])):
            race = race_lookup.get(key)
            if not race:
                continue
            trend = race_training_trend_text(rows)
            detail = " / ".join(
                f"{r['finish_order']}着 {r['horse_name']}: {training_pattern_text(r)}（{training_type_label(r['training_type'])} {display_date(r['training_date'])}）"
                for r in rows
            )
            lines.append(f"|{race.track}|{race.race_num}|{race.condition}|{trend}|{detail}|")
        lines.append("")

    lines.append("## 馬体重傾向")
    lines.append("")
    lines.append("- 区分: 結果確認 / おすすめスコアには未使用。馬体重の最適帯・増減耐性は競馬場×馬場で異なるため分けて集計します。当日発表後に確認する材料です。")
    lines.append("")
    for label, notes in body_weight_groups(by_track_surface):
        lines.append(f"### {label}")
        for note in notes:
            lines.append(f"- {note}")
        lines.append("")
    lines.append("### 全体（参考）")
    for note in body_weight_notes(races):
        lines.append(f"- {note}")
    lines.append("")

    status = next_pick_data_status(next_date, next_rows, next_horses, recommendations, pick_notice)
    lines.append("## 翌日おすすめ 前提データ")
    lines.append("")
    for line in next_pick_status_lines(status):
        lines.append(f"- {line}")
    lines.append("")
    if reference_recommendations:
        lines.append(f"## 翌日おすすめ 参考候補（{REFERENCE_CANDIDATE_MIN_SCORE}-{RECOMMENDATION_MIN_SCORE - 1}点）")
        lines.append("")
        lines.append("|場|R|馬番|馬名|評価|根拠|")
        lines.append("|---|---:|---:|---|---:|---|")
        for rec in reference_recommendations:
            h = rec.horse
            lines.append(
                f"|{h.track}|{h.race_num}|{h.horse_num}|{h.name}|{rec.score}|"
                f"{' / '.join(rec.reasons)}|"
            )
        lines.append("")

    lines.append("## レース別一覧")
    lines.append("")
    lines.append("|場|R|条件|勝ち馬|人気|枠|脚質|上がり|単勝|馬連|三連複|")
    lines.append("|---|---:|---|---|---:|---:|---|---:|---:|---:|---:|")
    for race in races:
        winner = race.winner
        if not winner:
            continue
        final_rank = winner.final3f_rank if winner.final3f_rank else "-"
        lines.append(
            f"|{race.track}|{race.race_num}|{race.condition}|{winner.name}|"
            f"{winner.popularity or '-'}|{winner.frame or '-'}|{winner.style or '-'}|{final_rank}|"
            f"{fmt_money(race.tan_payout)}|{fmt_money(race.umaren_payout)}|{fmt_money(race.sanrenpuku_payout)}|"
        )
    lines.append("")
    lines.append("## 読み方")
    lines.append("- 予測利用可は、出馬表段階で確認できる特徴を翌日の確認ポイントとして使う欄です。")
    lines.append("- 結果確認は、着順・上がり・当日3着内馬の追い切りなど、レース後に判明する後付け検証です。")
    lines.append("- 持ち越し信頼度は前日傾向を翌日に使う強さの目安です。中=確認ポイントとして使う、低=単独では使わない、対象外=予測に使わない。")
    lines.append("- 推奨スコアでは、中信頼は等倍、低信頼の枠・血統は0.35倍、個別追い切りのプラス評価は0.40倍に抑えています。")
    lines.append("- 枠帯は 内=1-3枠 / 中=4-6枠 / 外=7-8枠。")
    lines.append("- 枠・脚質は3着内頭数だけでなく、出走頭数を母数にした馬券内率で見ています。")
    lines.append("- 取消・除外・競走中止など異常区分付きの馬は、枠・脚質・血統の母数から除外しています。")
    lines.append("- 脚質はDBの脚質コード基準で、実際の通過順そのものではありません。")
    lines.append("- 上がりは結果確定後に分かる事後情報です。翌日の予想根拠ではなく、結果確認として扱ってください。")
    lines.append("- レース別の追い切り欄は3着内馬だけの後付け確認です。出走全馬平均との差分ではありません。")
    lines.append("- サンプルが少ない距離帯は、強い断定ではなく翌日の確認ポイントとして扱ってください。")
    lines.append(f"- おすすめ馬は評価{RECOMMENDATION_MIN_SCORE}点以上のみ表示します。閾値は簡易スコアで、バックテスト済みの期待値ではありません。")
    lines.append(f"- 参考候補は評価{REFERENCE_CANDIDATE_MIN_SCORE}-{RECOMMENDATION_MIN_SCORE - 1}点の馬です。推奨扱いではなく、確認用に表示しています。")
    lines.append("- 馬体重は当日発表後に確認する結果確認項目です。おすすめスコアには含めていません。")
    lines.append("")
    return "\n".join(lines)


def build_next_section(
    next_date: str,
    next_rows: list[sqlite3.Row],
    by_track_surface: dict[tuple[str, str], list[RaceResult]],
    by_track_surface_band: dict[tuple[str, str, str], list[RaceResult]],
) -> list[str]:
    lines = ["## 翌日レース別 適用メモ", ""]
    lines.append(f"対象日: {display_date(next_date)}")
    lines.append("")
    lines.append("|場|R|条件|使う傾向|メモ（持ち越し信頼度）|")
    lines.append("|---|---:|---|---|---|")
    for row in next_rows:
        track_code = str(row["track_code"])
        surface = surface_from_code(row["track_type_code"] or "")
        distance = int(row["distance"] or 0)
        band = distance_band(surface, distance)
        race_num = int(row["race_num"] or 0)
        track = TRACK_NAMES.get(track_code, track_code)
        band_group = by_track_surface_band.get((track_code, surface, band), [])
        surface_group = by_track_surface.get((track_code, surface), [])
        stats = summarize_if_sufficient(f"{track} {surface} {band}", band_group)
        if stats:
            source = trend_scope_label(surface, band)
        else:
            stats = summarize_if_sufficient(f"{track} {surface}", surface_group)
            source = f"{surface}全体" if stats else "-"
        if stats:
            memo = compact_note_text(predictive_trend_notes(stats))
        elif band_group or surface_group:
            memo = "同場同馬場の当日サンプル不足"
        else:
            memo = "同場同馬場の当日サンプルなし"
        lines.append(f"|{track}|{race_num}|{surface}{distance}m|{source}|{memo}|")
    lines.append("")
    return lines


def html_section_bounds(parts: list[str], section_id: str) -> tuple[int, int] | None:
    marker = f'<section id="{section_id}"'
    start = next((idx for idx, part in enumerate(parts) if marker in part), None)
    if start is None:
        return None
    for idx in range(start + 1, len(parts)):
        if parts[idx] == "</section>":
            return start, idx + 1
    return None


def move_html_section_after(parts: list[str], section_id: str, after_section_id: str) -> None:
    source_bounds = html_section_bounds(parts, section_id)
    if source_bounds is None:
        return
    source_start, source_end = source_bounds
    section = parts[source_start:source_end]
    del parts[source_start:source_end]

    target_bounds = html_section_bounds(parts, after_section_id)
    if target_bounds is None:
        parts[source_start:source_start] = section
        return
    _target_start, target_end = target_bounds
    parts[target_end:target_end] = section


def build_html(
    date_key: str,
    db_path: Path,
    races: list[RaceResult],
    next_date: str | None,
    next_rows: list[sqlite3.Row],
    recommendations: list[Recommendation],
    reference_recommendations: list[Recommendation] | None = None,
    trend_weights: TrendWeights | None = None,
    next_horses: list[NextHorse] | None = None,
    training_notes: list[str] | None = None,
    training_by_race: dict[tuple[str, int], list[dict]] | None = None,
    notice: str | None = None,
    pick_notice: str | None = None,
    score_bands: list[ScoreBandStat] | None = None,
) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    by_track_surface: dict[tuple[str, str], list[RaceResult]] = defaultdict(list)
    by_track_surface_band: dict[tuple[str, str, str], list[RaceResult]] = defaultdict(list)
    for race in races:
        by_track_surface[(race.track_code, race.surface)].append(race)
        by_track_surface_band[(race.track_code, race.surface, race.band)].append(race)

    blood_stats = summarize_bloodlines(races)
    race_lookup = {(race.track_code, race.race_num): race for race in races}
    rec_by_race: dict[tuple[str, int], list[Recommendation]] = defaultdict(list)
    for rec in recommendations:
        rec_by_race[(rec.horse.track_code, rec.horse.race_num)].append(rec)
    ref_by_race: dict[tuple[str, int], list[Recommendation]] = defaultdict(list)
    for rec in reference_recommendations or []:
        ref_by_race[(rec.horse.track_code, rec.horse.race_num)].append(rec)
    pick_status = next_pick_data_status(next_date, next_rows, next_horses, recommendations, pick_notice)
    next_summaries = build_next_race_summaries(next_rows, rec_by_race, ref_by_race)
    trend_weights = trend_weights or determine_trend_weights(races)
    track_surface_stats = [
        (f"{TRACK_NAMES.get(track_code, track_code)} {surface}", summarize(f"{TRACK_NAMES.get(track_code, track_code)} {surface}", group))
        for (track_code, surface), group in sorted(by_track_surface.items())
    ]

    css = """
:root{--bg:#f5f6f8;--ink:#17202a;--muted:#5b6b7e;--line:#d8e0ea;--card:#fff;--soft:#f1f5f9;--blue:#174ea6;--blue-bg:#e8f0fe;--warn:#fff7ed;--warn-line:#fed7aa;--accent:#2563eb}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
html,body{max-width:100%;overflow-x:hidden}
body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.6;font-size:15px;letter-spacing:0}
p,li,td,th,strong,span,div,a{overflow-wrap:anywhere}
[id]{scroll-margin-top:76px}
a:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:2px;border-radius:8px}
.wrap{max-width:1040px;margin:0 auto;padding:14px 14px 84px}
.hero{background:#0f172a;color:#fff;padding:18px 16px;border-radius:0 0 12px 12px}
.hero h1{font-size:21px;line-height:1.3;margin:0 0 8px;letter-spacing:0}
.meta{color:#cbd5e1;font-size:13px}
.nav{position:sticky;top:0;background:rgba(245,246,248,.97);backdrop-filter:blur(8px);z-index:5;padding:10px 0;display:flex;flex-wrap:nowrap;gap:8px;overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none}
.nav::-webkit-scrollbar{display:none}
.nav a{flex:0 0 auto;text-decoration:none;color:#0f172a;background:#fff;border:1px solid #dbe1e8;border-radius:999px;padding:9px 13px;font-size:13px;line-height:1.2;font-weight:700}
.to-top{position:fixed;right:12px;bottom:12px;z-index:6;background:rgba(15,23,42,.92);color:#fff;text-decoration:none;border-radius:999px;padding:11px 15px;font-size:13px;font-weight:700;box-shadow:0 2px 10px rgba(15,23,42,.3)}
.section{margin:18px 0}
.section h2{font-size:17px;margin:0 0 10px}
.section h3{font-size:15px;margin:14px 0 8px}
.card h4{font-size:14px;margin:0 0 6px;color:#0f172a}
.grid{display:grid;gap:10px;min-width:0}
.dashboard-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
.metric{min-width:0;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:9px}
.metric b{display:block;color:var(--muted);font-size:12px;line-height:1.2;margin-bottom:3px}
.metric span{display:block;color:#0f172a;font-size:17px;font-weight:800;line-height:1.25}
.metric small{display:block;color:var(--muted);font-size:12px;margin-top:2px}
.decision{margin-top:10px;border-left:4px solid var(--accent);background:#f8fbff;padding:10px 11px;border-radius:6px}
.decision b{display:block;font-size:13px;color:var(--blue);margin-bottom:2px}
.decision span{font-size:14px;font-weight:700;color:#0f172a}
.card,details.race{width:100%;max-width:100%;min-width:0;background:var(--card);border:1px solid #e2e8f0;border-radius:8px;box-shadow:0 1px 2px rgba(15,23,42,.04)}
.card{padding:12px}
.quick-card{padding-top:10px}
details.race{margin:8px 0}
details.race.recommended{border-left:4px solid var(--accent)}
details.race.reference{border-left:4px solid #f59e0b}
details.race summary{cursor:pointer;list-style:none;padding:12px}
details.race summary::-webkit-details-marker{display:none}
details.race>summary .pill::after{content:" ▾"}
details.race[open]>summary .pill::after{content:" ▴"}
.summary-main{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:start}
.summary-main>div{min-width:0}
.summary-main strong{font-size:15px;line-height:1.35}
.summary-condition{color:var(--muted);font-size:13px;margin-top:2px}
.summary-picks{color:#334155;font-size:14px;margin-top:4px;line-height:1.35;font-weight:700}
.training-insight{margin-top:6px;font-size:14px;font-weight:700;color:#0f172a}
.tag-row{display:flex;gap:5px;flex-wrap:wrap;margin-top:7px}
.tag{display:inline-flex;align-items:center;border-radius:999px;background:var(--blue-bg);color:var(--blue);padding:4px 9px;font-size:12px;font-weight:700;line-height:1.2}
.tag.muted{background:var(--soft);color:#475569}
.detail-body{padding:0 12px 12px;min-width:0}
.pick-mini{max-width:100%;border-left:4px solid var(--accent);border-top:1px solid #e2e8f0;margin-top:10px;padding:10px 0 0 8px}
.race-title{display:block;min-width:0}
.race-title strong{font-size:15px}
.pill{display:inline-flex;align-items:center;border-radius:6px;padding:4px 8px;background:var(--blue-bg);color:var(--blue);font-weight:700;font-size:12px;white-space:nowrap}
.sub{color:var(--muted);font-size:14px;line-height:1.5}
.horse{font-size:15px;font-weight:800;margin:2px 0;line-height:1.4}
.chips{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;margin-top:8px;max-width:100%}
.chip{min-width:0;max-width:100%;background:var(--soft);border-radius:6px;padding:6px 8px;font-size:13px;line-height:1.35}
.chip.strong{background:var(--blue-bg);color:var(--blue);font-weight:800}
.chip.wide{grid-column:1/-1}
.race-group{margin-top:8px;background:#fff;border:1px solid #cbd5e1;border-radius:8px;overflow:hidden}
.race-group>summary{cursor:pointer;display:flex;align-items:center;justify-content:space-between;gap:8px;padding:13px 12px;background:#f8fafc;font-weight:800;color:#0f172a}
.race-group>summary::after{content:"開く ▾";flex:none;border-radius:999px;background:var(--blue-bg);color:var(--blue);padding:5px 10px;font-size:12px;line-height:1.2}
.race-group[open]>summary::after{content:"閉じる ▴"}
.race-group-body{padding:0 12px 12px}
.race-group-body .race-overview{margin-top:10px}
.race-overview-card{margin-top:10px}
.race-overview{display:grid;gap:6px}
.pick-jump{display:flex;flex-wrap:wrap;gap:7px;margin:10px 0 4px}
.pick-jump a{display:inline-flex;align-items:center;text-decoration:none;color:#0f172a;background:#f8fbff;border:1px solid #bfdbfe;border-radius:8px;padding:9px 12px;font-size:13px;font-weight:800;line-height:1.2}
.race-row{display:grid;grid-template-columns:minmax(118px,1.1fr) 52px 98px minmax(0,1.5fr);gap:6px;align-items:center;padding:9px 8px;border:1px solid var(--line);border-radius:6px;background:#fff}
.race-row.no-pick{background:#f8fafc;color:var(--muted)}
.race-row.has-pick{background:#f8fbff;border-color:#bfdbfe}
.race-row.has-reference{background:#fffbeb;border-color:#fde68a}
.race-cell{min-width:0;font-size:13px;line-height:1.3}
.race-cell.time{white-space:nowrap;color:var(--muted)}
.race-cell.condition{color:#475569}
.race-cell.pick{font-weight:700;color:#0f172a}
.race-link{font-weight:800;color:#0f172a;text-decoration:none}
.race-link.muted{color:#475569}
.raw-training{margin-top:8px;border:1px solid #e2e8f0;border-radius:8px;background:#f8fafc}
.raw-training summary{padding:9px 10px;font-size:13px;color:#475569;cursor:pointer}
.raw-training .raw-body{padding:0 10px 10px;font-size:13px;color:#334155;line-height:1.5}
.reason{margin:8px 0 0;padding-left:18px;font-size:14px}
.reason li{margin:3px 0}
.stat{display:grid;grid-template-columns:86px minmax(0,1fr);gap:6px 10px;font-size:14px}
.stat b{color:#475569}
.usage-label{margin-top:8px;font-size:13px;font-weight:800;color:var(--blue)}
.usage-label.result{color:#92400e}
.notes{padding-left:18px;margin:6px 0;font-size:14px}
.notes li{margin:4px 0}
.table-wrap{width:100%;overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:#fff}
.table{width:100%;min-width:620px;border-collapse:collapse;background:#fff}
.table th,.table td{border-bottom:1px solid #e2e8f0;padding:9px 10px;text-align:left;font-size:14px;vertical-align:top}
.table th{background:#f8fafc;color:#475569;white-space:nowrap}
.table tr:last-child td{border-bottom:0}
.table th.num,.table td.num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.table.band-table{min-width:0;table-layout:fixed}
.band-table td,.band-table th{padding:9px 6px}
.band-table th:first-child,.band-table td:first-child{width:34%}
.band-table th.num,.band-table td.num{width:22%}
.trend-table td:nth-child(2){white-space:nowrap;text-align:right;color:#475569}
.small{font-size:13px;color:var(--muted)}
.scroll-hint{display:none;margin:0 0 6px}
.empty{background:var(--warn);border-color:var(--warn-line)}
@media(min-width:760px){.grid.cols{grid-template-columns:repeat(2,minmax(0,1fr))}.dashboard-grid{grid-template-columns:repeat(4,minmax(0,1fr))}.hero h1{font-size:24px}.chips{grid-template-columns:repeat(4,minmax(0,1fr))}.nav{flex-wrap:wrap;overflow-x:visible}}
@media(max-width:640px){.scroll-hint{display:block}}
@media(max-width:520px){.wrap{padding:10px 10px 84px}.card{padding:10px}.nav{gap:6px;padding:8px 0}.nav a{font-size:13px;padding:8px 11px}.summary-main{grid-template-columns:minmax(0,1fr)}.pill{width:max-content}.table{min-width:560px}.trend-table{min-width:0}.trend-table thead{display:none}.trend-table,.trend-table tbody,.trend-table tr,.trend-table td{display:block;width:100%}.trend-table tr{padding:8px;border-bottom:1px solid #e2e8f0}.trend-table tr:last-child{border-bottom:0}.trend-table td{border-bottom:0;padding:3px 0}.trend-table td:nth-child(1){font-weight:800}.trend-table td:nth-child(2){text-align:left}.trend-table td:nth-child(2)::before{content:"R ";font-weight:800;color:#475569}.trend-table td:nth-child(3)::before{content:"予測 ";font-weight:800;color:#174ea6}.trend-table td:nth-child(4)::before{content:"結果 ";font-weight:800;color:#92400e}.race-row{grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"main time" "condition condition" "pick pick";align-items:start;row-gap:4px}.race-cell.main{grid-area:main}.race-cell.time{grid-area:time;text-align:right;white-space:nowrap}.race-cell.condition{grid-area:condition}.race-cell.pick{grid-area:pick;text-align:left}.race-overview{gap:6px}}
"""

    parts: list[str] = [
        "<!doctype html>",
        '<html lang="ja">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta name="color-scheme" content="light">',
        '<meta name="theme-color" content="#0f172a">',
        f"<title>中央競馬 傾向 {e(display_date(date_key))}</title>",
        f"<style>{css}</style>",
        "</head>",
        "<body>",
        '<div class="hero" id="top">',
        f"<h1>中央競馬 傾向レポート<br>{e(display_date(date_key))}</h1>",
        f'<div class="meta">生成 {e(generated_at)} / 対象 {len(races)}R / 元DB {e(db_path.name)}</div>',
        "</div>",
        '<main class="wrap">',
        '<nav class="nav" aria-label="ページ内リンク"><a href="#overview">概要</a><a href="#picks">レース別おすすめ</a><a href="#track">場別傾向</a><a href="#blood">血統</a><a href="#training">追い切り</a><a href="#bodyweight">馬体重</a><a href="#results">当日結果</a><a href="#guide">読み方</a></nav>',
    ]

    overview_card_class = "card dashboard empty" if pick_status.zero_reason or pick_status.warnings else "card dashboard"
    parts.append('<section id="overview" class="section">')
    parts.append("<h2>概要</h2>")
    parts.append(f'<article class="{overview_card_class}">')
    parts.append('<div class="dashboard-grid">')
    for label, value in overview_status_items(date_key, len(races), pick_status):
        parts.append(f'<div class="metric"><b>{e(label)}</b><span>{e(value)}</span></div>')
    parts.append("</div>")
    parts.append('<div class="decision"><b>最初に見るポイント</b>')
    parts.append(f"<span>{e(overview_decision_text(pick_status))}</span></div>")
    # 詳細な注意・0件理由は「翌日おすすめ 前提データ」に1回だけ表示する（重複回避）。
    parts.append("</article>")
    parts.append("</section>")

    if notice:
        notice_html = "<br>".join(e(part) for part in notice.splitlines())
        # 閉じ </section> を独立要素にする。1行に埋め込むと html_section_bounds が
        # notice の境界を検出できず、picks の移動先が track の後ろにずれてしまう。
        parts.append('<section id="notice" class="section">')
        parts.append(f'<article class="card empty"><strong>注意</strong><br>{notice_html}</article>')
        parts.append("</section>")

    parts.append('<section id="track" class="section">')
    parts.append("<h2>場別傾向</h2>")
    append_html_weight_card(parts, trend_weights)
    append_html_track_trend_overview(parts, track_surface_stats)
    parts.append("<h3>傾向詳細</h3>")
    parts.append('<div class="grid cols">')
    for _label, stats in track_surface_stats:
        parts.append('<article class="card">')
        parts.append(f"<h3>{e(stats.label)}</h3>")
        append_html_note_groups(parts, trend_notes(stats)[:4])
        sample_ok = trend_sample_sufficient(stats)
        bucket_label = "枠別率" if sample_ok else "枠3着内"
        style_label = "脚質率" if sample_ok else "脚質3着内"
        parts.append('<div class="stat">')
        parts.append(f"<b>R数</b><span>{len(stats.races)}</span>")
        parts.append(f"<b>{e(bucket_label)}</b><span>{e(ratio_counter_text(stats.top3_buckets, stats.starter_buckets, include_rate=sample_ok))}</span>")
        parts.append(f"<b>{e(style_label)}</b><span>{e(ratio_counter_text(stats.top3_styles, stats.starter_styles, include_rate=sample_ok))}</span>")
        parts.append(f"<b>上がり</b><span>結果確認 {e(final3f_result_text(stats, include_rate=sample_ok))}</span>")
        if not sample_ok:
            parts.append("<b>率表示</b><span>サンプル不足のため非表示</span>")
        parts.append("</div>")
        parts.append("</article>")
    parts.append("</div>")
    parts.append("</section>")

    parts.append('<section id="blood" class="section">')
    parts.append("<h2>血統傾向</h2>")
    parts.append('<p class="sub">区分: 予測利用可 / 持ち越し信頼度: 低。血統は出馬表段階で確認可能ですが、表示条件を満たすサンプルだけを扱います。</p>')
    parts.append('<div class="grid cols">')
    for label, notes in bloodline_groups(by_track_surface):
        parts.append('<article class="card">')
        parts.append(f"<h3>{e(label)}</h3>")
        parts.append('<ul class="notes">')
        for note in notes[:4]:
            parts.append(f"<li>{e(format_carryover_note(note, bloodline_carryover_confidence(note)))}</li>")
        parts.append("</ul>")
        parts.append("</article>")
    parts.append("</div>")
    parts.append('<details class="race-group">')
    parts.append("<summary>血統全体（参考）を見る</summary>")
    parts.append('<div class="race-group-body"><ul class="notes">')
    for note in bloodline_notes(blood_stats):
        parts.append(f"<li>{e(format_carryover_note(note, bloodline_carryover_confidence(note)))}</li>")
    parts.append("</ul></div></details>")
    parts.append("</section>")

    parts.append('<section id="training" class="section">')
    parts.append("<h2>最終追い切り傾向</h2>")
    parts.append('<p class="sub">区分: 結果確認 / 持ち越し対象外。当日3着内馬ベースの後付け集計で、翌日予測へ直接使う材料ではありません。</p>')
    note_list = training_notes or ["追い切りデータなし"]
    main_note_list = [note for note in note_list if not note.startswith("参考")]
    reference_note_list = [note for note in note_list if note.startswith("参考")]
    group_class = "race-group empty" if any("ありません" in note or "取り込める" in note for note in note_list) else "race-group"
    parts.append(f'<details class="{group_class}">')
    parts.append("<summary>追い切り全体メモを見る</summary>")
    parts.append('<div class="race-group-body"><ul class="notes">')
    for note in main_note_list:
        parts.append(f"<li>{e(note)}</li>")
    parts.append("</ul>")
    if reference_note_list:
        parts.append('<details class="raw-training"><summary>参考時計を見る</summary><div class="raw-body">')
        for note in reference_note_list:
            parts.append(f"{e(note)}<br>")
        parts.append("</div></details>")
    parts.append("</div></details>")
    training_race_items = []
    if training_by_race:
        for key, rows in sorted(training_by_race.items(), key=lambda item: (item[0][0], item[0][1])):
            race = race_lookup.get(key)
            if race:
                training_race_items.append((race, rows))
    if training_race_items:
        parts.append('<p class="small">レース別欄は3着内馬だけの後付け確認です。出走全馬平均との差分ではありません。</p>')
        training_items_by_track: dict[str, list[tuple[RaceResult, list[dict]]]] = defaultdict(list)
        for race, rows in training_race_items:
            training_items_by_track[race.track_code].append((race, rows))
        for track_code, track_items in sorted(training_items_by_track.items()):
            track_name = track_items[0][0].track if track_items else TRACK_NAMES.get(track_code, track_code)
            parts.append('<details class="race-group">')
            parts.append(f'<summary>{e(track_name)} 追い切りレース別詳細（{len(track_items)}R分）</summary>')
            parts.append('<div class="race-group-body">')
            for race, rows in track_items:
                summary = race_training_trend_text(rows)
                parts.append('<details class="race">')
                parts.append("<summary>")
                parts.append('<div class="summary-main">')
                parts.append(f"<div><strong>{e(race_label(race.track, race.race_num, race.race_name))}</strong><div class=\"summary-condition\">{e(race.condition)}</div><div class=\"training-insight\">馬券内馬の要約: {e(summary)}</div></div>")
                parts.append('<span class="pill">馬別</span>')
                parts.append("</div>")
                parts.append("</summary>")
                parts.append('<div class="detail-body">')
                for r in rows:
                    parts.append('<div class="pick-mini">')
                    parts.append(f'<div class="horse">{r["finish_order"]}着 {e(r["horse_name"])}</div>')
                    parts.append('<div class="tag-row">')
                    for tag in training_pattern_tags(r):
                        parts.append(f'<span class="tag">{e(tag)}</span>')
                    parts.append("</div>")
                    parts.append('<div class="chips">')
                    parts.append(f'<span class="chip">{e(training_type_label(r["training_type"]))}</span>')
                    parts.append(f'<span class="chip">{e(training_course_text(r))}</span>')
                    if r["training_date"]:
                        parts.append(f'<span class="chip">{e(display_date(r["training_date"]))}</span>')
                    parts.append("</div>")
                    parts.append('<details class="raw-training">')
                    parts.append("<summary>時計詳細</summary>")
                    parts.append(f'<div class="raw-body">時計: {e(training_work_summary(r))}<br>ラップ: {e(training_lap_text(r))}</div>')
                    parts.append("</details>")
                    parts.append("</div>")
                parts.append("</div></details>")
            parts.append("</div></details>")
    parts.append("</section>")

    parts.append('<section id="bodyweight" class="section">')
    parts.append("<h2>馬体重</h2>")
    append_html_body_weight_card(parts, body_weight_groups(by_track_surface), body_weight_notes(races))
    parts.append("</section>")

    parts.append('<section id="picks" class="section">')
    parts.append("<h2>翌日レース別おすすめ馬</h2>")
    if next_date:
        parts.append(f'<p class="sub">対象日 {e(display_date(next_date))}。評価{RECOMMENDATION_MIN_SCORE}点以上を各レース最大3頭表示。</p>')
    else:
        parts.append(f'<p class="sub">評価{RECOMMENDATION_MIN_SCORE}点以上を表示対象にしています。</p>')

    status_card_class = "card empty" if pick_status.zero_reason else "card"
    parts.append(f'<article class="{status_card_class}">')
    parts.append("<h3>前提データ</h3>")
    parts.append('<div class="stat">')
    for label, value in next_pick_status_items(pick_status):
        parts.append(f"<b>{e(label)}</b><span>{e(value)}</span>")
    parts.append("</div>")
    if pick_status.zero_reason:
        parts.append(f'<p class="sub"><strong>推奨0件の理由</strong>: {e(pick_status.zero_reason)}</p>')
    for warning in dict.fromkeys(pick_status.warnings):
        parts.append(f'<p class="sub"><strong>注意</strong>: {e(warning)}</p>')
    parts.append("</article>")

    append_html_score_band_note(parts, score_bands or [])

    if next_summaries:
        append_html_race_overview(parts, next_summaries)
        recommended_summaries = [summary for summary in next_summaries if summary.recommendations]
        if recommended_summaries:
            parts.append('<div class="pick-jump">')
            for summary in recommended_summaries:
                parts.append(
                    f'<a href="#{e(summary.race_id)}">'
                    f'{e(race_label(summary.track, summary.race_num, summary.race_name))}</a>'
                )
            parts.append("</div>")
        if recommended_summaries:
            parts.append("<h3>推奨あり詳細</h3>")
            for idx, summary in enumerate(recommended_summaries):
                append_html_recommendation_detail(parts, summary, open_detail=(idx == 0))
        else:
            parts.append(
                '<article class="card empty"><h3>推奨あり詳細</h3>'
                '<p class="sub">条件を満たす推奨馬がないため、空のレース詳細は省略しています。</p></article>'
            )
        reference_summaries = [summary for summary in next_summaries if summary.reference_recommendations]
        if reference_summaries:
            parts.append(f"<h3>参考候補（{REFERENCE_CANDIDATE_MIN_SCORE}-{RECOMMENDATION_MIN_SCORE - 1}点）</h3>")
            for summary in reference_summaries:
                append_html_recommendation_detail(parts, summary, reference=True)
    else:
        if not pick_status.zero_reason:
            message = "実行日以降の出馬表がDBにないため、おすすめ馬は作成していません。JRA-VAN更新後に再実行してください。"
            parts.append(f'<article class="card empty">{e(message)}</article>')
    parts.append("</section>")

    parts.append('<section id="results" class="section">')
    parts.append("<h2>当日結果一覧</h2>")
    parts.append('<p class="small scroll-hint">表は横にスクロールできます</p>')
    parts.append('<div class="table-wrap">')
    parts.append('<table class="table"><thead><tr><th>場</th><th class="num">R</th><th>条件</th><th>勝ち馬</th><th class="num">人気</th><th class="num">三連複配当</th></tr></thead><tbody>')
    for race in races:
        winner = race.winner
        if not winner:
            continue
        parts.append(
            f'<tr><td>{e(race.track)}</td><td class="num">{race.race_num}</td><td>{e(race.condition)}</td>'
            f'<td>{e(winner.name)}</td><td class="num">{winner.popularity or "-"}</td><td class="num">{e(fmt_money(race.sanrenpuku_payout))}</td></tr>'
        )
    parts.append("</tbody></table></div>")
    parts.append("</section>")

    parts.append('<section id="guide" class="section">')
    parts.append("<h2>このレポートの読み方</h2>")
    parts.append('<article class="card">')
    parts.append('<ul class="notes">')
    parts.append("<li>おすすめ馬は「当日傾向との一致度」を点数化したものです。馬券の的中や購入を保証するものではありません。</li>")
    parts.append("<li>「予測利用可」は出馬表段階で分かる特徴（翌日の予測に使えます）、「結果確認」はレース後に判明する後付けの検証です。</li>")
    parts.append("<li>「持ち越し信頼度」は 中=確認しながら使う / 低=単独では使わない / 対象外=予測に使わない の目安です。</li>")
    parts.append("<li>サンプル数が少ない条件は、断定材料にせず確認ポイントとして扱ってください。</li>")
    parts.append("</ul>")
    parts.append("</article>")
    parts.append("</section>")
    move_html_section_after(parts, "picks", "notice" if notice else "overview")
    parts.append('<a class="to-top" href="#top" aria-label="ページの先頭へ戻る">↑ トップ</a>')
    parts.append("</main></body></html>")
    return "\n".join(parts)


def write_race_csv(output_path: Path, races: list[RaceResult]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "date",
                "track",
                "race_num",
                "race_name",
                "condition",
                "winner_num",
                "winner_name",
                "winner_popularity",
                "winner_frame",
                "winner_style",
                "winner_final3f_rank",
                "top3_frames",
                "top3_popularities",
                "tan_payout",
                "umaren_payout",
                "sanrenpuku_payout",
            ]
        )
        for race in races:
            winner = race.winner
            if not winner:
                continue
            writer.writerow(
                [
                    display_date(race.date),
                    race.track,
                    race.race_num,
                    race.race_name,
                    race.condition,
                    winner.horse_num,
                    winner.name,
                    winner.popularity,
                    winner.frame,
                    winner.style,
                    winner.final3f_rank,
                    " ".join(str(h.frame) for h in race.top3),
                    " ".join(str(h.popularity) for h in race.top3 if h.popularity),
                    race.tan_payout,
                    race.umaren_payout,
                    race.sanrenpuku_payout,
                ]
            )


RECOMMENDATION_LOG_FIELDS = [
    "generated_at",
    "source_date",
    "target_date",
    "track_code",
    "track",
    "race_num",
    "race_name",
    "horse_num",
    "horse_name",
    "score",
    "trend_source",
    "reasons",
    "odds",
    "popularity",
    "frame",
    "jockey",
    "surface",
    "distance",
    "finish_order",
    "result_status",
    "in_top3",
    "win",
    "win_return",
    "place_return",
    "evaluated_at",
]


def recommendation_log_rows(
    source_date: str,
    target_date: str,
    recommendations: list[Recommendation],
    generated_at: str | None = None,
) -> list[dict[str, str]]:
    generated_at = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows: list[dict[str, str]] = []
    for rec in recommendations:
        horse = rec.horse
        rows.append(
            {
                "generated_at": generated_at,
                "source_date": source_date,
                "target_date": target_date,
                "track_code": horse.track_code,
                "track": horse.track,
                "race_num": str(horse.race_num),
                "race_name": horse.race_name,
                "horse_num": horse.horse_num,
                "horse_name": horse.name,
                "score": str(rec.score),
                "trend_source": rec.trend_source,
                "reasons": " / ".join(rec.reasons),
                "odds": f"{horse.odds:.1f}" if horse.odds else "",
                "popularity": str(horse.popularity) if horse.popularity else "",
                "frame": str(horse.frame) if horse.frame else "",
                "jockey": horse.jockey,
                "surface": horse.surface,
                "distance": str(horse.distance) if horse.distance else "",
                "finish_order": "",
                "result_status": "",
                "in_top3": "",
                "win": "",
                "win_return": "",
                "place_return": "",
                "evaluated_at": "",
            }
        )
    return rows


def read_recommendation_log(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return [{field: row.get(field, "") for field in RECOMMENDATION_LOG_FIELDS} for row in csv.DictReader(f)]


# 過去実績注記で使うスコア帯。evaluate_recommendations.py の集計と境界を合わせる。
SCORE_BAND_DEFS: list[tuple[str, int, int]] = [
    ("90点以上", 90, 10_000),
    ("80-89点", 80, 90),
    ("70-79点", 70, 80),
    ("62-69点", 62, 70),
]
# この件数に満たない帯は「参考」注記を付ける。
SCORE_BAND_REFERENCE_SAMPLE = 15
# 全帯合計の馬券対象がこの件数に満たない場合は注記自体を出さない。
SCORE_BAND_MIN_TOTAL = 30


@dataclass
class ScoreBandStat:
    label: str
    bet_count: int
    top3: int
    place_return: int

    @property
    def top3_rate(self) -> str:
        return pct(self.top3, self.bet_count)

    @property
    def place_return_rate(self) -> str:
        return pct(self.place_return, self.bet_count * 100)

    @property
    def is_reference(self) -> bool:
        return self.bet_count < SCORE_BAND_REFERENCE_SAMPLE


def _log_row_score(row: dict[str, str]) -> int | None:
    text = str(row.get("score", "")).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def score_band_performance(rows: list[dict[str, str]]) -> list[ScoreBandStat]:
    """評価済み・馬券対象の推奨だけを対象に、スコア帯別の複勝実績を集計する。"""
    bettable = [
        row
        for row in rows
        if row.get("result_status") and row.get("result_status") != "対象馬なし"
    ]
    stats: list[ScoreBandStat] = []
    for label, low, high in SCORE_BAND_DEFS:
        band_rows = [row for row in bettable if (score := _log_row_score(row)) is not None and low <= score < high]
        if not band_rows:
            continue
        top3 = sum(1 for row in band_rows if str(row.get("in_top3", "")).strip().lower() == "true")
        place_return = sum(row_int(row, "place_return") for row in band_rows)
        stats.append(ScoreBandStat(label, len(band_rows), top3, place_return))
    return stats


def write_recommendation_log(
    path: Path,
    source_date: str,
    target_date: str,
    recommendations: list[Recommendation],
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_rows = [
        row
        for row in read_recommendation_log(path)
        if not (row.get("source_date") == source_date and row.get("target_date") == target_date)
    ]
    new_rows = recommendation_log_rows(source_date, target_date, recommendations)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=RECOMMENDATION_LOG_FIELDS)
        writer.writeheader()
        writer.writerows(existing_rows)
        writer.writerows(new_rows)
    return len(new_rows)


def recommendation_result_status(order: int) -> str:
    if order == 1:
        return "勝ち"
    if 2 <= order <= 3:
        return "3着内"
    if order > 3:
        return "圏外"
    return "対象馬なし"


def update_recommendation_results(
    path: Path,
    target_date: str,
    races: list[RaceResult],
    evaluated_at: str | None = None,
) -> int:
    rows = read_recommendation_log(path)
    if not rows or not races:
        return 0
    evaluated_at = evaluated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    race_lookup = {(race.track_code, race.race_num): race for race in races}
    race_keys = set(race_lookup)
    horse_lookup: dict[tuple[str, int, str], HorseResult] = {}
    for race in races:
        for horse in race.horses:
            horse_lookup[(race.track_code, race.race_num, horse.horse_num)] = horse

    updated = 0
    for row in rows:
        if row.get("target_date") != target_date:
            continue
        try:
            race_num = int(row.get("race_num") or 0)
        except ValueError:
            continue
        track_code = row.get("track_code", "")
        if (track_code, race_num) not in race_keys:
            continue
        race = race_lookup[(track_code, race_num)]
        horse = horse_lookup.get((track_code, race_num, row.get("horse_num", "")))
        order = horse.order if horse else 0
        status = recommendation_result_status(order)
        row["finish_order"] = str(order) if order else ""
        row["result_status"] = status
        row["in_top3"] = "true" if 1 <= order <= 3 else "false"
        row["win"] = "true" if order == 1 else "false"
        row["win_return"] = str(race.tan_payouts.get(horse.horse_num, 0)) if horse else "0"
        row["place_return"] = str(race.fuku_payouts.get(horse.horse_num, 0)) if horse else "0"
        row["evaluated_at"] = evaluated_at
        updated += 1

    if updated:
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=RECOMMENDATION_LOG_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    return updated


def report_paths(output_dir: Path, date_key: str) -> tuple[Path, Path, Path]:
    report_dir = output_dir / date_key
    return (
        report_dir / REPORT_FILE_MAP["trend"],
        report_dir / REPORT_FILE_MAP["races"],
        report_dir / REPORT_FILE_MAP["mobile"],
    )


def migrate_flat_outputs(root: Path, *, history_dir_name: str | None = None) -> int:
    """Move old YYYYMMDD_kind.ext files into date folders to keep roots readable."""
    if not root.exists():
        return 0
    moved = 0
    pattern = re.compile(r"^(\d{8})_(mobile|trend|races)(?: \d+)?\.(html|md|csv)$")
    base = root / history_dir_name if history_dir_name else root
    for path in list(root.iterdir()):
        if not path.is_file():
            continue
        match = pattern.match(path.name)
        if not match:
            continue
        date_key, kind, _ext = match.groups()
        dest_dir = base / date_key
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / REPORT_FILE_MAP[kind]
        if dest.exists():
            dest = dest_dir / path.name
        path.replace(dest)
        moved += 1
    return moved


def publish_outputs(paths: list[Path], publish_dir: Path, date_key: str, html_path: Path | None = None) -> list[Path]:
    publish_dir.mkdir(parents=True, exist_ok=True)
    published: list[Path] = []
    history_dir = publish_dir / "history" / date_key
    history_dir.mkdir(parents=True, exist_ok=True)
    for path in paths:
        if not path.exists():
            continue
        dst = history_dir / path.name
        shutil.copy2(path, dst)
        published.append(dst)
    if html_path and html_path.exists():
        latest = publish_dir / "index.html"
        shutil.copy2(html_path, latest)
        published.append(latest)
    return published


def main() -> int:
    config = load_config()
    parser = argparse.ArgumentParser(description="中央競馬の当日傾向をMarkdown/CSVに集計します")
    parser.add_argument("--date", help="集計日。YYYYMMDD / YYYY-MM-DD / today")
    parser.add_argument("--latest-completed", action="store_true", help="結果確定済みの最新開催日を自動選択")
    parser.add_argument("--fallback-latest", action="store_true", help="指定日の結果が未確定なら最新確定日に切り替える")
    parser.add_argument("--next-date", help="翌日適用メモに使う日付。未指定なら次にDBへ登録済みの開催日")
    parser.add_argument(
        "--intraday-date",
        help="当日途中結果を軽量補正に使う日付。YYYYMMDD / today / next。未指定なら補正なし",
    )
    parser.add_argument("--db", default=config["source_db"], help="keiba.db のパス")
    parser.add_argument("--output-dir", default=config["output_dir"], help="出力フォルダ")
    parser.add_argument("--publish-dir", default=config["publish_dir"], help="iCloudなどへコピーする公開フォルダ")
    parser.add_argument("--no-publish", action="store_true", help="iCloudへのコピーを行わない")
    parser.add_argument(
        "--min-races",
        type=int,
        default=int(config.get("latest_min_races", MIN_LATEST_COMPLETED_RACES)),
        help=f"最新日判定に必要な最低レース数（下限{MIN_LATEST_COMPLETED_RACES}R）",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    output_dir = Path(args.output_dir)
    publish_dir = Path(args.publish_dir)
    latest_min_races = effective_latest_min_races(args.min_races)
    expected_races_by_date = config.get("expected_races_by_date", {})

    requested_date_key: str | None = None
    fallback_notice: str | None = None
    notices: list[str] = []
    pick_notice: str | None = None
    if args.min_races < latest_min_races:
        notices.append(
            f"最新確定日の自動判定は最低{latest_min_races}Rを必須にしています。"
            f"設定値 {args.min_races}R は部分取り込み防止のため引き上げました。"
        )

    with connect(db_path) as conn:
        if args.latest_completed or not args.date:
            date_key = latest_completed_date(conn, latest_min_races, expected_races_by_date)
            latest_db_date = latest_race_date(conn)
            if latest_db_date and latest_db_date > date_key:
                latest_status = date_status(conn, latest_db_date)
                if latest_status["complete_races"] < latest_status["races"]:
                    # 運用者向けの技術メッセージ（着順未入力・再同期指示）は共有ページに不要なので出さない。
                    # 閲覧者向けの説明は pick_notice（前提データ）に集約する。
                    pick_notice = f"{display_date(latest_db_date)} は結果待ち（出走前）です。前開催 {display_date(date_key)} の傾向でおすすめを作成しています。"
        else:
            date_key = normalize_date(args.date)
            requested_date_key = date_key

        status = date_status(conn, date_key)
        if status["races"] <= 0 or status["top3_races"] < status["races"]:
            message = no_result_message(date_key, status)
            if args.fallback_latest:
                fallback_date = latest_completed_date(conn, latest_min_races, expected_races_by_date)
                fallback_notice = f"{message} 最新確定日の {display_date(fallback_date)} に切り替えます。"
                notices.append(fallback_notice)
                date_key = fallback_date
            else:
                raise RuntimeError(message)
        status = date_status(conn, date_key)
        warning = payout_warning(date_key, status)
        if warning:
            notices.append(warning)

        rows = load_result_rows(conn, date_key)
        races = build_races(rows)
        if not races:
            status = date_status(conn, date_key)
            message = no_result_message(date_key, status)
            if args.fallback_latest:
                fallback_date = latest_completed_date(conn, latest_min_races, expected_races_by_date)
                fallback_notice = f"{message} 最新確定日の {display_date(fallback_date)} に切り替えます。"
                notices.append(fallback_notice)
                date_key = fallback_date
                rows = load_result_rows(conn, date_key)
                races = build_races(rows)
            if not races:
                raise RuntimeError(message)

        if args.next_date:
            next_date = normalize_date(args.next_date)
        else:
            next_date = next_available_race_date(conn, date_key, min_date=datetime.now().strftime("%Y%m%d"))
        if args.intraday_date:
            if args.intraday_date.lower() == "next":
                intraday_date = next_date
            else:
                intraday_date = normalize_date(args.intraday_date)
        else:
            intraday_date = None
        next_rows = load_next_races(conn, next_date) if next_date else []
        next_horses = load_next_horses(conn, next_date) if next_date else []
        if next_date and next_horses:
            attach_next_training(conn, next_horses, next_date)
            attach_jockey_course_stats(conn, next_horses, next_date)
        training_notes = training_trend_notes(conn, date_key)
        training_by_race = load_training_by_race(conn, date_key)

    by_track: dict[str, list[RaceResult]] = defaultdict(list)
    by_track_surface: dict[tuple[str, str], list[RaceResult]] = defaultdict(list)
    by_track_surface_band: dict[tuple[str, str, str], list[RaceResult]] = defaultdict(list)
    for race in races:
        by_track[race.track_code].append(race)
        by_track_surface[(race.track_code, race.surface)].append(race)
        by_track_surface_band[(race.track_code, race.surface, race.band)].append(race)
    trend_weights = determine_trend_weights(races)
    target_year = int((next_date or date_key)[:4])
    apply_historical_validation_weights(trend_weights, validation_summary_path_for_target(output_dir, target_year))
    weight_conn = connect(db_path)
    try:
        apply_historical_mining_weight(weight_conn, trend_weights, date_key)
        apply_intraday_result_weights(weight_conn, trend_weights, intraday_date)
    finally:
        weight_conn.close()
    disable_mining_weight_if_missing(trend_weights, next_horses)
    adjust_weights_for_next_data(trend_weights, next_horses)
    recommendations = build_recommendations(
        next_horses,
        by_track,
        by_track_surface,
        by_track_surface_band,
        weights=trend_weights,
    )
    reference_recommendations = build_recommendations(
        next_horses,
        by_track,
        by_track_surface,
        by_track_surface_band,
        weights=trend_weights,
        min_score=REFERENCE_CANDIDATE_MIN_SCORE,
        max_score=RECOMMENDATION_MIN_SCORE,
    )
    trend_weights.applied_counts = recommendation_reason_counts(recommendations)

    migrate_flat_outputs(output_dir)
    md_path, csv_path, html_path = report_paths(output_dir, date_key)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    recommendation_log_path = output_dir / RECOMMENDATION_LOG_FILE
    # build_html より前に読むことで、当日ぶんの未評価行が混ざらない過去実績だけを集計できる。
    score_bands = score_band_performance(read_recommendation_log(recommendation_log_path))
    notice_text = "\n".join(dict.fromkeys(notices)) if notices else None
    md_path.write_text(
        build_markdown(
            date_key,
            db_path,
            races,
            next_date,
            next_rows,
            training_notes=training_notes,
            training_by_race=training_by_race,
            notice=notice_text,
            next_horses=next_horses,
            recommendations=recommendations,
            reference_recommendations=reference_recommendations,
            trend_weights=trend_weights,
            pick_notice=pick_notice,
        ),
        encoding="utf-8-sig",
    )
    write_race_csv(csv_path, races)
    html_path.write_text(
        build_html(
            date_key,
            db_path,
            races,
            next_date,
            next_rows,
            recommendations,
            reference_recommendations=reference_recommendations,
            trend_weights=trend_weights,
            next_horses=next_horses,
            training_notes=training_notes,
            training_by_race=training_by_race,
            notice=notice_text,
            pick_notice=pick_notice,
            score_bands=score_bands,
        ),
        encoding="utf-8",
    )
    logged_recommendations = write_recommendation_log(
        recommendation_log_path,
        date_key,
        next_date,
        recommendations,
    ) if next_date else 0
    evaluated_recommendations = update_recommendation_results(recommendation_log_path, date_key, races)

    published: list[Path] = []
    should_publish = bool(config.get("publish_to_icloud", True)) and not args.no_publish
    if should_publish:
        migrate_flat_outputs(publish_dir, history_dir_name="history")
        published = publish_outputs([html_path, md_path, csv_path], publish_dir, date_key, html_path=html_path)
        if recommendation_log_path.exists():
            publish_recommendation_log = publish_dir / recommendation_log_path.name
            shutil.copy2(recommendation_log_path, publish_recommendation_log)
            published.append(publish_recommendation_log)

    # GitHub Pages 用に最新レポートを docs/index.html へも書き出す（git 公開はバッチが担当）。
    docs_index: Path | None = None
    if bool(config.get("publish_to_docs", True)) and not args.no_publish:
        docs_dir = Path(config.get("docs_dir") or (APP_DIR / "docs"))
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / ".nojekyll").touch()
        docs_index = docs_dir / "index.html"
        shutil.copy2(html_path, docs_index)

    if notice_text:
        print(f"注意: {notice_text}")
    if requested_date_key and requested_date_key != date_key:
        print(f"指定日: {display_date(requested_date_key)} -> 集計日: {display_date(date_key)}")
    print(f"集計日: {display_date(date_key)}")
    print(f"レース数: {len(races)}")
    print(f"おすすめ馬: {len(recommendations)}件")
    print(f"参考候補: {len(reference_recommendations)}件")
    if next_date:
        print(f"おすすめ検証ログ: {recommendation_log_path} ({logged_recommendations}件記録)")
    if evaluated_recommendations:
        print(f"おすすめ結果反映: {evaluated_recommendations}件")
    pick_status = next_pick_data_status(next_date, next_rows, next_horses, recommendations, pick_notice)
    print(
        "おすすめ前提: "
        f"出馬表{pick_status.race_count}R / "
        f"出走馬{pick_status.horse_count}頭 / "
        f"追い切り{pick_status.training_count}頭 / "
        f"DM{pick_status.mining_count}頭"
    )
    if pick_status.zero_reason:
        print(f"おすすめ0件理由: {pick_status.zero_reason}")
    for warning in dict.fromkeys(pick_status.warnings):
        print(f"おすすめ前提注意: {warning}")
    print(f"HTML: {html_path}")
    print(f"Markdown: {md_path}")
    print(f"CSV: {csv_path}")
    if published:
        print(f"iCloud出力: {publish_dir}")
        print(f"iCloud最新HTML: {publish_dir / 'index.html'}")
    if docs_index:
        print(f"docs出力: {docs_index}")
    if next_date and next_rows:
        print(f"翌日おすすめ対象: {display_date(next_date)} ({len(next_rows)}R)")
    elif next_date:
        print(f"翌日おすすめ対象: {display_date(next_date)} のレース番組がDBにありません")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
