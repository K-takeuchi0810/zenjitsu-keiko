import csv
import sqlite3
import tempfile
import unittest
from collections import Counter, defaultdict
from pathlib import Path
from unittest.mock import patch

import collect_trends as c


def horse(
    num: int,
    *,
    order: int = 0,
    frame: int | None = None,
    style: str = "先行",
    sire: str = "",
    dam_sire: str = "",
    horse_weight: int = 0,
    weight_change: int | None = None,
    mining_rank: int = 0,
) -> c.HorseResult:
    return c.HorseResult(
        horse_num=str(num),
        frame=frame or ((num - 1) % 8 + 1),
        name=f"馬{num}",
        order=order,
        popularity=num,
        final3f=350 + num if order else 0,
        style=style,
        sire_name=sire,
        dam_sire_name=dam_sire,
        horse_weight=horse_weight,
        weight_change=weight_change,
        mining_rank=mining_rank,
    )


def race(
    *,
    track_code: str = "05",
    race_num: int = 1,
    surface: str = "ダート",
    distance: int = 1600,
    starters: int = 16,
    band: str | None = None,
) -> c.RaceResult:
    horses = []
    for num in range(1, starters + 1):
        order = num if num <= 3 else 0
        style = "逃げ" if num == 1 else "先行" if num <= 6 else "差し"
        horses.append(horse(num, order=order, style=style))
    return c.RaceResult(
        date="20260530",
        track_code=track_code,
        race_num=race_num,
        race_name="",
        surface=surface,
        distance=distance,
        band=band or c.distance_band(surface, distance),
        weather="晴",
        ground="良",
        horses=horses,
        tan_payout=100,
        umaren_payout=500,
        sanrenpuku_payout=1000,
    )


def next_horse(
    *,
    track_code: str = "05",
    race_num: int = 1,
    surface: str = "ダート",
    distance: int = 1200,
    frame: int = 5,
    horse_num: str = "1",
    name: str = "次走馬",
    odds: float = 5.0,
    popularity: int = 1,
    mining_rank: int = 1,
    training: dict | None = None,
    style: str = "先行",
) -> c.NextHorse:
    return c.NextHorse(
        track_code=track_code,
        race_num=race_num,
        race_name="",
        surface=surface,
        distance=distance,
        band=c.distance_band(surface, distance),
        start_time="10:00",
        horse_num=horse_num,
        blood_register_num="B1",
        frame=frame,
        name=name,
        jockey_code="J1",
        jockey="騎手",
        odds=odds,
        popularity=popularity,
        style=style,
        mining_rank=mining_rank,
        training=training,
    )


def next_race_row(
    *,
    track_code: str = "05",
    race_num: int = 1,
    track_type_code: str = "23",
    distance: int = 1200,
) -> dict:
    return {
        "track_code": track_code,
        "race_num": f"{race_num:02d}",
        "race_name": "",
        "race_short10": "",
        "track_type_code": track_type_code,
        "distance": distance,
        "start_time": "1000",
    }


def result_row(
    num: int,
    *,
    order: int = 0,
    abnormal_code: str = "0",
    sire: str = "",
    dam_sire: str = "",
) -> dict:
    return {
        "race_year": "2026",
        "race_month_day": "0530",
        "track_code": "05",
        "kaiji": "01",
        "nichiji": "01",
        "race_num": "01",
        "race_name": "",
        "race_short10": "",
        "track_type_code": "23",
        "distance": 1600,
        "weather_code": "1",
        "turf_condition": "0",
        "dirt_condition": "1",
        "horse_num": f"{num:02d}",
        "waku_num": ((num - 1) % 8) + 1,
        "horse_name": f"馬{num}",
        "finish_order": order,
        "confirmed_order": order,
        "win_popularity": num,
        "final_3f": 350 + num if order else 0,
        "leg_quality_code": "2",
        "abnormal_code": abnormal_code,
        "sire_name": sire,
        "dam_sire_name": dam_sire,
        "tan_payout1": 100,
        "umaren_payout1": 500,
        "sanrenpuku_payout1": 1000,
    }


def training_row(name: str, finish_order: int) -> dict:
    return {
        "finish_order": finish_order,
        "horse_name": name,
        "training_date": "20260529",
        "training_type": "slope",
        "training_center_code": "0",
        "course_code": "",
        "course_direction": "",
        "times_total": 560,
        "time_10f": 0,
        "time_9f": 0,
        "time_8f": 0,
        "time_7f": 0,
        "time_6f": 0,
        "time_5f": 0,
        "time_4f": 560,
        "time_3f": 420,
        "time_2f": 280,
        "lap_10f_9f": 0,
        "lap_9f_8f": 0,
        "lap_8f_7f": 0,
        "lap_7f_6f": 0,
        "lap_6f_5f": 0,
        "lap_5f_4f": 0,
        "lap_4f_3f": 140,
        "lap_3f_2f": 140,
        "lap_2f_1f": 140,
        "lap_1f": 140,
    }


def grouped(races: list[c.RaceResult]):
    by_track: dict[str, list[c.RaceResult]] = defaultdict(list)
    by_track_surface: dict[tuple[str, str], list[c.RaceResult]] = defaultdict(list)
    by_track_surface_band: dict[tuple[str, str, str], list[c.RaceResult]] = defaultdict(list)
    for item in races:
        by_track[item.track_code].append(item)
        by_track_surface[(item.track_code, item.surface)].append(item)
        by_track_surface_band[(item.track_code, item.surface, item.band)].append(item)
    return by_track, by_track_surface, by_track_surface_band


def scoring_stats() -> c.TrendStats:
    return c.TrendStats(
        label="test",
        races=[race(race_num=i, starters=8) for i in range(1, 4)],
        starter_count=24,
        starter_frames=Counter(),
        starter_buckets=Counter({"内": 9, "中": 9, "外": 6}),
        starter_styles=Counter(),
        winner_frames=Counter(),
        top3_frames=Counter(),
        winner_buckets=Counter(),
        top3_buckets=Counter({"内": 9}),
        winner_styles=Counter(),
        top3_styles=Counter(),
        winner_popularities=[],
        top3_popularities=[],
        fast3f_top3=0,
        top3_count=9,
        high_payout_races=0,
    )


def scoring_horse(**overrides) -> c.NextHorse:
    values = {
        "track_code": "05",
        "race_num": 1,
        "race_name": "",
        "surface": "ダート",
        "distance": 1600,
        "band": "マイル",
        "start_time": "10:00",
        "horse_num": "1",
        "blood_register_num": "B1",
        "frame": 1,
        "name": "評価馬",
        "jockey_code": "J1",
        "jockey": "騎手",
        "odds": 0.0,
        "popularity": 0,
        "style": "",
        "mining_rank": 0,
    }
    values.update(overrides)
    return c.NextHorse(**values)


class LatestCompletedDateTests(unittest.TestCase):
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
            CREATE TABLE payouts (
                race_year TEXT,
                race_month_day TEXT,
                track_code TEXT,
                kaiji TEXT,
                nichiji TEXT,
                race_num TEXT,
                tan_payout1 INTEGER,
                umaren_payout1 INTEGER,
                sanrenpuku_payout1 INTEGER
            );
            """
        )

    def tearDown(self) -> None:
        self.conn.close()

    def insert_completed_race(self, date_key: str, race_num: int, track_code: str = "05") -> None:
        year, month_day = date_key[:4], date_key[4:]
        params = (year, month_day, track_code, "01", "01", f"{race_num:02d}")
        self.conn.execute("INSERT INTO races VALUES (?, ?, ?, ?, ?, ?)", params)
        for order in (1, 2, 3):
            self.conn.execute(
                "INSERT INTO horse_races VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (*params, order, order),
            )
        self.conn.execute(
            "INSERT INTO payouts VALUES (?, ?, ?, ?, ?, ?, 100, 500, 1000)",
            params,
        )

    def test_latest_completed_requires_at_least_24_races_even_if_12_requested(self):
        for race_num in range(1, 13):
            self.insert_completed_race("20260531", race_num)
        for race_num in range(1, 25):
            self.insert_completed_race("20260530", race_num)

        self.assertEqual(c.effective_latest_min_races(12), 24)
        self.assertEqual(c.latest_completed_date(self.conn, 12), "20260530")

    def test_latest_completed_rejects_two_track_partial_when_neighbor_has_three_tracks(self):
        for track_code in ("05", "08", "09"):
            for race_num in range(1, 13):
                self.insert_completed_race("20260530", race_num, track_code)
        for track_code in ("05", "08"):
            for race_num in range(1, 13):
                self.insert_completed_race("20260531", race_num, track_code)

        self.assertEqual(c.expected_races_for_date(self.conn, "20260531", 24), 36)
        self.assertEqual(c.latest_completed_date(self.conn, 24), "20260530")

    def test_configured_expected_races_can_reject_missing_track_without_neighbor(self):
        for race_num in range(1, 25):
            self.insert_completed_race("20260530", race_num)
        for race_num in range(1, 25):
            self.insert_completed_race("20260531", race_num)

        self.assertEqual(
            c.latest_completed_date(self.conn, 24, {"20260531": 36}),
            "20260530",
        )


class RecommendationSampleTests(unittest.TestCase):
    def test_small_band_sample_falls_back_to_sufficient_surface_sample(self):
        races = [
            race(race_num=1, distance=1200),
            race(race_num=2, distance=1200),
            race(race_num=3, distance=1800),
        ]
        by_track, by_track_surface, by_track_surface_band = grouped(races)

        stats, source = c.choose_trend_stats(
            next_horse(distance=1200),
            by_track,
            by_track_surface,
            by_track_surface_band,
        )

        self.assertIsNotNone(stats)
        self.assertEqual(source, "ダート全体")
        self.assertTrue(c.trend_sample_sufficient(stats))

    def test_no_sufficient_sample_scores_zero(self):
        races = [race(race_num=1, distance=1200), race(race_num=2, distance=1200)]
        by_track, by_track_surface, by_track_surface_band = grouped(races)
        horse_to_score = next_horse(distance=1200)
        stats, source = c.choose_trend_stats(
            horse_to_score,
            by_track,
            by_track_surface,
            by_track_surface_band,
        )

        rec = c.score_horse(horse_to_score, stats, source)

        self.assertIsNone(stats)
        self.assertEqual(rec.score, 0)


class RecommendationScoringConfidenceTests(unittest.TestCase):
    def test_frame_score_add_is_reduced_as_low_confidence(self):
        rec = c.score_horse(scoring_horse(frame=1), scoring_stats(), "test")

        self.assertEqual(rec.score, 43)
        self.assertTrue(any("低信頼・加点抑制" in reason for reason in rec.reasons))

    def test_training_score_add_is_reduced(self):
        training = training_row("評価馬", 1)
        training.update({"lap_1f": 120, "lap_2f_1f": 130, "time_4f": 530})

        rec = c.score_horse(scoring_horse(frame=8, training=training), scoring_stats(), "test")

        self.assertEqual(rec.score, 44)
        self.assertTrue(any("調教:" in reason and "加点抑制" in reason for reason in rec.reasons))

    def test_training_slowdown_penalty_is_lightweight(self):
        training = training_row("評価馬", 1)
        training.update({"lap_1f": 160, "lap_2f_1f": 130})

        rec = c.score_horse(scoring_horse(frame=8, training=training), scoring_stats(), "test")

        self.assertEqual(rec.score, 37)
        self.assertTrue(any("調教終い失速" in reason for reason in rec.reasons))

    def test_bloodline_score_add_is_reduced_as_low_confidence(self):
        bloodline_stats = c.BloodlineStats(
            sire_starters=Counter({"Strong": 5, "Other": 15}),
            sire_top3=Counter({"Strong": 3}),
            sire_wins=Counter(),
            dam_sire_starters=Counter(),
            dam_sire_top3=Counter(),
            dam_sire_wins=Counter(),
            starter_count=20,
            top3_count=3,
            race_count=3,
        )

        rec = c.score_horse(
            scoring_horse(frame=8, sire_name="Strong"),
            scoring_stats(),
            "test",
            bloodline_stats,
        )

        self.assertEqual(rec.score, 43)
        self.assertTrue(any("父Strong" in reason and "低信頼・加点抑制" in reason for reason in rec.reasons))


class DisplayGuardTests(unittest.TestCase):
    def test_low_sample_markdown_hides_rates(self):
        low_sample_race = race(surface="障害", distance=3000, starters=13)
        markdown = c.build_markdown(
            "20260530",
            Path("dummy.db"),
            [low_sample_race],
            None,
            [],
            training_notes=[],
            training_by_race={},
        )

        self.assertIn("|枠帯別 3着内数|", markdown)
        self.assertIn("|率表示|サンプル不足のため非表示|", markdown)
        self.assertNotIn("1/1頭(100%)", markdown)
        self.assertNotIn("1勝(100%)", markdown)

    def test_low_sample_html_hides_specific_rate_text(self):
        low_sample_race = race(surface="障害", distance=3000, starters=13)
        html = c.build_html(
            "20260530",
            Path("dummy.db"),
            [low_sample_race],
            None,
            [],
            [],
            training_notes=[],
            training_by_race={},
        )

        self.assertIn("枠3着内", html)
        self.assertIn("サンプル不足のため非表示", html)
        self.assertNotIn("1/1頭(100%)", html)


class RecommendationPrerequisiteTests(unittest.TestCase):
    def test_status_explains_no_pick_when_threshold_is_not_met(self):
        status = c.next_pick_data_status(
            "20260531",
            [next_race_row()],
            [next_horse(training=training_row("次走馬", 0))],
            [],
        )

        self.assertEqual(status.race_count, 1)
        self.assertEqual(status.horse_count, 1)
        self.assertEqual(status.odds_count, 1)
        self.assertEqual(status.popularity_count, 1)
        self.assertEqual(status.training_count, 1)
        self.assertIn(f"評価{c.RECOMMENDATION_MIN_SCORE}点以上の馬がありません", status.zero_reason)
        self.assertTrue(any("推奨0件の理由" in line for line in c.next_pick_status_lines(status)))

    def test_status_explains_missing_market_data(self):
        status = c.next_pick_data_status(
            "20260531",
            [next_race_row()],
            [next_horse(odds=0.0, popularity=0)],
            [],
        )

        self.assertIn("オッズ・人気が未取得", status.zero_reason)
        self.assertTrue(any("オッズが未取得" in warning for warning in status.warnings))
        self.assertTrue(any("人気が未取得" in warning for warning in status.warnings))

    def test_markdown_and_html_include_prerequisite_section(self):
        base_races = [race(race_num=i) for i in range(1, 4)]
        next_rows = [next_race_row()]
        next_horses = [next_horse()]

        markdown = c.build_markdown(
            "20260530",
            Path("dummy.db"),
            base_races,
            "20260531",
            next_rows,
            training_notes=[],
            training_by_race={},
            next_horses=next_horses,
            recommendations=[],
        )
        html = c.build_html(
            "20260530",
            Path("dummy.db"),
            base_races,
            "20260531",
            next_rows,
            [],
            next_horses=next_horses,
            training_notes=[],
            training_by_race={},
        )

        self.assertIn("## 翌日おすすめ 前提データ", markdown)
        self.assertIn("推奨0件の理由", markdown)
        self.assertIn('id="overview"', html)
        self.assertIn("<h2>概要</h2>", html)
        self.assertIn("<b>集計日</b>", html)
        self.assertIn("<b>推奨馬</b>", html)
        self.assertIn("<b>オッズ</b>", html)
        self.assertIn("前提データ", html)
        self.assertIn("推奨0件の理由", html)
        self.assertNotIn("全場合算傾向", html)
        self.assertNotIn('href="#summary"', html)
        self.assertNotIn('id="summary"', html)
        self.assertLess(html.index('href="#picks"'), html.index('href="#track"'))
        self.assertLess(html.index('id="overview"'), html.index('id="picks"'))
        self.assertLess(html.index('id="picks"'), html.index('id="track"'))


class RecommendationLogTests(unittest.TestCase):
    def read_rows(self, path: Path) -> list[dict[str, str]]:
        with path.open("r", newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    def test_recommendation_log_replaces_same_source_and_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recommendation_log.csv"
            first = [c.Recommendation(next_horse(horse_num="1", name="初回"), 70, "東京 ダート", ["理由1"])]
            second = [c.Recommendation(next_horse(horse_num="2", name="更新後"), 80, "東京 ダート", ["理由2"])]

            self.assertEqual(c.write_recommendation_log(path, "20260530", "20260531", first), 1)
            self.assertEqual(c.write_recommendation_log(path, "20260530", "20260531", second), 1)
            rows = self.read_rows(path)

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["horse_num"], "2")
            self.assertEqual(rows[0]["horse_name"], "更新後")
            self.assertEqual(rows[0]["score"], "80")

    def test_recommendation_log_updates_actual_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recommendation_log.csv"
            recs = [
                c.Recommendation(next_horse(horse_num="1", name="馬1"), 75, "東京 ダート", ["理由1"]),
                c.Recommendation(next_horse(horse_num="9", name="取消候補"), 72, "東京 ダート", ["理由2"]),
            ]
            c.write_recommendation_log(path, "20260530", "20260531", recs)
            result_race = c.RaceResult(
                date="20260531",
                track_code="05",
                race_num=1,
                race_name="",
                surface="ダート",
                distance=1200,
                band=c.distance_band("ダート", 1200),
                weather="晴",
                ground="良",
                horses=[
                    horse(1, order=2),
                    horse(2, order=1),
                    horse(3, order=3),
                    horse(4, order=4),
                ],
                tan_payouts={"2": 340},
                fuku_payouts={"1": 150, "2": 120, "3": 180},
            )

            updated = c.update_recommendation_results(path, "20260531", [result_race], evaluated_at="2026-05-31 00:00")
            rows = {row["horse_num"]: row for row in self.read_rows(path)}

            self.assertEqual(updated, 2)
            self.assertEqual(rows["1"]["finish_order"], "2")
            self.assertEqual(rows["1"]["result_status"], "3着内")
            self.assertEqual(rows["1"]["in_top3"], "true")
            self.assertEqual(rows["1"]["win"], "false")
            self.assertEqual(rows["1"]["win_return"], "0")
            self.assertEqual(rows["1"]["place_return"], "150")
            self.assertEqual(rows["9"]["result_status"], "対象馬なし")
            self.assertEqual(rows["9"]["in_top3"], "false")
            self.assertEqual(rows["9"]["win_return"], "0")
            self.assertEqual(rows["9"]["place_return"], "0")


class TrendUsageClassificationTests(unittest.TestCase):
    def test_markdown_and_html_split_predictive_and_result_check_notes(self):
        races = [race(race_num=i) for i in range(1, 4)]

        markdown = c.build_markdown(
            "20260530",
            Path("dummy.db"),
            races,
            None,
            [],
            training_notes=[],
            training_by_race={},
        )
        html = c.build_html(
            "20260530",
            Path("dummy.db"),
            races,
            None,
            [],
            [],
            training_notes=[],
            training_by_race={},
        )

        self.assertIn("**予測利用可**", markdown)
        self.assertIn("**結果確認**", markdown)
        self.assertIn("持ち越し信頼度: 中", markdown)
        self.assertIn("|距離帯|R|予測利用可|結果確認|", markdown)
        self.assertIn("区分: 結果確認", markdown)
        self.assertIn("区分: 予測利用可 / 持ち越し信頼度: 低", markdown)
        self.assertIn("区分: 結果確認 / 持ち越し対象外", markdown)
        self.assertIn("usage-label pred", html)
        self.assertIn("usage-label result", html)
        self.assertIn("持ち越し信頼度: 中", html)
        self.assertIn("区分: 結果確認", html)

    def test_carryover_confidence_labels(self):
        self.assertEqual(c.carryover_confidence("前目脚質の馬券内率高め（逃げ・先行 9/32頭、28%）"), "中")
        self.assertEqual(c.carryover_confidence("荒れ気味（高配当レース 5/6）"), "中")
        self.assertEqual(c.carryover_confidence("内枠の馬券内率高め（4/15頭、27%）"), "低")
        self.assertEqual(c.carryover_confidence("結果確認: 上がり3位内が3着内の61%"), "対象外")
        self.assertEqual(c.carryover_confidence("サンプル1R/9頭のため傾向断定不可"), "対象外")

    def test_next_section_uses_only_predictive_notes(self):
        races = [race(race_num=i) for i in range(1, 4)]
        _by_track, by_track_surface, by_track_surface_band = grouped(races)
        section = "\n".join(
            c.build_next_section(
                "20260531",
                [
                    {
                        "track_code": "05",
                        "track_type_code": "23",
                        "distance": 1600,
                        "race_num": "01",
                    }
                ],
                by_track_surface,
                by_track_surface_band,
            )
        )

        self.assertIn("前目脚質", section)
        self.assertIn("持ち越し信頼度: 中", section)
        self.assertNotIn("結果確認", section)


class DynamicWeightingTests(unittest.TestCase):
    def test_trend_weights_emphasize_day_specific_biases(self):
        races = [race(race_num=i, starters=8) for i in range(1, 5)]

        weights = c.determine_trend_weights(races)

        self.assertGreater(weights.frame, 1.0)
        self.assertGreater(weights.style, 1.0)
        self.assertTrue(any("枠:" in note for note in weights.notes))
        self.assertTrue(any("脚質:" in note for note in weights.notes))

    def test_body_weight_notes_are_report_only(self):
        weighted_race = c.RaceResult(
            date="20260530",
            track_code="05",
            race_num=1,
            race_name="",
            surface="芝",
            distance=1600,
            band="マイル",
            weather="晴",
            ground="良",
            horses=[
                horse(1, order=1, horse_weight=510, weight_change=12),
                horse(2, order=2, horse_weight=440, weight_change=-2),
                horse(3, order=3, horse_weight=430, weight_change=-10),
                horse(4, order=4, horse_weight=500, weight_change=0),
            ],
        )

        notes = c.body_weight_notes([weighted_race])
        html = c.build_html("20260530", Path("dummy.db"), [weighted_race], None, [], [])

        self.assertTrue(any("馬体重取得済み" in note for note in notes))
        self.assertTrue(any("大幅増減" in note for note in notes))
        self.assertIn("<h2>馬体重</h2>", html)
        self.assertIn("おすすめスコアには未使用", html)

    def test_body_weight_is_split_by_track_and_surface(self):
        tokyo_turf = c.RaceResult(
            date="20260530", track_code="05", race_num=1, race_name="",
            surface="芝", distance=1600, band="マイル", weather="晴", ground="良",
            horses=[
                horse(1, order=1, horse_weight=500, weight_change=4),
                horse(2, order=2, horse_weight=480, weight_change=-2),
                horse(3, order=3, horse_weight=470, weight_change=0),
                horse(4, order=4, horse_weight=460, weight_change=2),
            ],
        )
        hanshin_dirt = c.RaceResult(
            date="20260530", track_code="09", race_num=1, race_name="",
            surface="ダート", distance=1400, band="短距離", weather="晴", ground="良",
            horses=[
                horse(1, order=1, horse_weight=520, weight_change=6),
                horse(2, order=2, horse_weight=510, weight_change=-4),
                horse(3, order=3, horse_weight=500, weight_change=0),
                horse(4, order=4, horse_weight=490, weight_change=2),
            ],
        )
        by_track_surface = {("05", "芝"): [tokyo_turf], ("09", "ダート"): [hanshin_dirt]}

        groups = c.body_weight_groups(by_track_surface)
        labels = [label for label, _ in groups]
        self.assertEqual(labels, ["東京 芝", "阪神 ダート"])
        for _label, notes in groups:
            self.assertTrue(any("馬体重取得済み" in note for note in notes))

        html = c.build_html("20260530", Path("dummy.db"), [tokyo_turf, hanshin_dirt], None, [], [])
        self.assertIn("競馬場×馬場で異なる", html)
        self.assertIn("<h4>東京 芝</h4>", html)
        self.assertIn("<h4>阪神 ダート</h4>", html)

    def test_reference_candidates_are_shown_separately_from_recommendations(self):
        base_races = [race(race_num=i) for i in range(1, 4)]
        candidate = c.Recommendation(next_horse(name="参考馬"), 60, "芝全体", ["理由"])

        html = c.build_html(
            "20260530",
            Path("dummy.db"),
            base_races,
            "20260531",
            [next_race_row()],
            [],
            reference_recommendations=[candidate],
            next_horses=[candidate.horse],
            training_notes=[],
            training_by_race={},
        )

        self.assertIn("参考候補", html)
        self.assertIn("参考馬", html)
        self.assertIn("推奨あり詳細", html)

    def test_missing_mining_data_is_reported_and_disabled(self):
        status = c.next_pick_data_status(
            "20260531",
            [next_race_row()],
            [next_horse(mining_rank=0)],
            [],
        )
        weights = c.TrendWeights(mining=1.2)
        c.disable_mining_weight_if_missing(weights, [next_horse(mining_rank=0)])

        self.assertEqual(status.mining_count, 0)
        self.assertTrue(any("データマイニング順位が未取得" in warning for warning in status.warnings))
        self.assertEqual(weights.mining, 0.0)

    def test_missing_next_style_data_disables_style_weight(self):
        weights = c.TrendWeights(style=1.3)

        c.adjust_weights_for_next_data(weights, [next_horse(style=""), next_horse(style="")])

        self.assertEqual(weights.style, 0.0)
        self.assertTrue(any("脚質データ未取得" in note for note in weights.notes))

    def test_historical_validation_summary_adjusts_weights_conservatively(self):
        weights = c.TrendWeights(style=1.0, frame=1.0, bloodline=1.08)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trend_validation_summary.csv"
            path.write_text(
                "\n".join(
                    [
                        "group,signal_type,signal_label,date_pairs,total_rows,judged,reproduced,partial,missed,unavailable,reproduction_rate,reproduced_or_partial_rate,sample_note",
                        "予測信号,style,脚質,9,52,52,41,9,2,0,79%,96%,参考値",
                        "予測信号,frame,枠,9,29,29,11,7,11,0,38%,62%,参考値",
                        "予測信号,bloodline,血統,9,29,29,9,7,13,0,31%,54%,通常",
                    ]
                ),
                encoding="utf-8-sig",
            )
            c.apply_historical_validation_weights(weights, path)

        self.assertGreater(weights.style, 1.0)
        self.assertLess(weights.frame, 1.0)
        self.assertLess(weights.bloodline, 0.9)
        self.assertTrue(any("過去検証" in note for note in weights.notes))

    def test_intraday_results_lightly_adjust_market_and_mining(self):
        races = []
        for i in range(1, 11):
            horses = []
            for num in range(1, 9):
                order = num if num <= 3 else 0
                horses.append(horse(num, order=order, mining_rank=num))
            races.append(
                c.RaceResult(
                    date="20260621",
                    track_code="05",
                    race_num=i,
                    race_name="",
                    surface="ダート",
                    distance=1600,
                    band="マイル",
                    weather="晴",
                    ground="良",
                    horses=horses,
                )
            )
        weights = c.TrendWeights()
        with patch.object(c, "load_result_rows", return_value=[]), patch.object(c, "build_races", return_value=races):
            c.apply_intraday_result_weights(sqlite3.connect(":memory:"), weights, "20260621")

        self.assertGreater(weights.market, 1.0)
        self.assertGreater(weights.mining, 1.0)
        self.assertTrue(any("当日途中結果" in note for note in weights.notes))


class BloodlineTests(unittest.TestCase):
    def test_three_of_three_bloodline_is_not_displayed(self):
        tiny = c.RaceResult(
            date="20260530",
            track_code="05",
            race_num=1,
            race_name="",
            surface="芝",
            distance=1600,
            band="マイル",
            weather="晴",
            ground="良",
            horses=[
                horse(1, order=1, sire="Tiny"),
                horse(2, order=2, sire="Tiny"),
                horse(3, order=3, sire="Tiny"),
            ],
        )

        notes = c.bloodline_notes(c.summarize_bloodlines([tiny]))

        self.assertTrue(any("サンプル不足" in note for note in notes))
        self.assertFalse(any("Tiny" in note for note in notes))

    def test_bloodline_requires_sample_and_rate_edge(self):
        horses = [
            horse(1, order=1, sire="Strong"),
            horse(2, order=2, sire="Strong"),
            horse(3, order=3, sire="Strong"),
            horse(4, sire="Strong"),
            horse(5, sire="Strong"),
        ]
        horses.extend(horse(num, sire="Other") for num in range(6, 31))
        strong = c.RaceResult(
            date="20260530",
            track_code="05",
            race_num=1,
            race_name="",
            surface="芝",
            distance=1600,
            band="マイル",
            weather="晴",
            ground="良",
            horses=horses,
        )

        notes = c.bloodline_notes(c.summarize_bloodlines([strong]))

        self.assertTrue(any("父 Strong" in note for note in notes))

    def test_html_keeps_overall_bloodline_collapsed_after_surface_groups(self):
        horses = [
            horse(1, order=1, sire="Strong"),
            horse(2, order=2, sire="Strong"),
            horse(3, order=3, sire="Strong"),
            horse(4, sire="Strong"),
            horse(5, sire="Strong"),
        ]
        horses.extend(horse(num, sire="Other") for num in range(6, 31))
        strong = c.RaceResult(
            date="20260530",
            track_code="05",
            race_num=1,
            race_name="",
            surface="芝",
            distance=1600,
            band="マイル",
            weather="晴",
            ground="良",
            horses=horses,
        )

        html = c.build_html("20260530", Path("dummy.db"), [strong], None, [], [])

        self.assertIn("<h3>東京 芝</h3>", html)
        self.assertIn("血統全体（参考）を見る", html)
        self.assertLess(html.index("<h3>東京 芝</h3>"), html.index("血統全体（参考）を見る"))
        self.assertNotIn("<h3>全体</h3>", html)
        self.assertNotIn('<details class="race-group" open', html)


class AbnormalStarterTests(unittest.TestCase):
    def test_build_races_excludes_abnormal_rows_from_starter_denominator(self):
        rows = [
            result_row(1, order=1, sire="Normal"),
            result_row(2, order=2, sire="Normal"),
            result_row(3, order=3, sire="Normal"),
            result_row(4, sire="Normal"),
            result_row(5, abnormal_code="4", sire="Excluded"),
        ]

        races = c.build_races(rows)
        stats = c.summarize("test", races)
        blood = c.summarize_bloodlines(races)

        self.assertEqual(len(races), 1)
        self.assertEqual(len(races[0].horses), 4)
        self.assertEqual(stats.starter_count, 4)
        self.assertNotIn("Excluded", blood.sire_starters)


class TrainingWordingTests(unittest.TestCase):
    def test_training_by_race_is_labeled_as_post_race_summary(self):
        base_race = race(race_num=1)
        kyoto_race = race(track_code="08", race_num=1)
        training_by_race = {
            ("05", 1): [
                training_row("馬1", 1),
                training_row("馬2", 2),
                training_row("馬3", 3),
            ],
            ("08", 1): [
                training_row("京都馬1", 1),
                training_row("京都馬2", 2),
                training_row("京都馬3", 3),
            ],
        }

        markdown = c.build_markdown(
            "20260530",
            Path("dummy.db"),
            [base_race, kyoto_race],
            None,
            [],
            training_notes=["追い切りテスト"],
            training_by_race=training_by_race,
        )
        html = c.build_html(
            "20260530",
            Path("dummy.db"),
            [base_race, kyoto_race],
            None,
            [],
            [],
            training_notes=["追い切りテスト"],
            training_by_race=training_by_race,
        )

        self.assertIn("レース別欄は3着内馬だけの後付け確認", markdown)
        self.assertIn("|場|R|条件|馬券内馬の要約|馬券内馬の型|", markdown)
        self.assertNotIn("|場|R|条件|調教傾向|馬券内馬の型|", markdown)
        self.assertIn("馬券内馬の要約:", html)
        self.assertIn("後付け確認", html)
        self.assertIn("追い切り全体メモを見る", html)
        self.assertLess(html.index("追い切り全体メモを見る"), html.index("追い切りテスト"))
        self.assertIn('<details class="race-group">', html)
        self.assertIn("追い切りレース別詳細（1R分）", html)
        self.assertIn("東京 追い切りレース別詳細（1R分）", html)
        self.assertIn("京都 追い切りレース別詳細（1R分）", html)
        self.assertLess(html.index("追い切りレース別詳細"), html.index("馬券内馬の要約:"))
        self.assertNotIn('<details class="race-group" open', html)
        self.assertNotIn('training-insight">傾向:', html)


class SchedulerBatchTests(unittest.TestCase):
    def test_scheduled_task_uses_no_pause(self):
        root = Path(__file__).resolve().parents[1]
        batch = (root / "sync_jvlink_then_collect.bat").read_text(encoding="utf-8")
        task_script = (root / "create_weekend_task.ps1").read_text(encoding="utf-8")
        validation_batch = (root / "run_weekly_validation_summary.bat").read_text(encoding="utf-8")
        validation_task = (root / "create_weekly_validation_task.ps1").read_text(encoding="utf-8")

        self.assertIn("--no-pause", batch)
        self.assertIn("NO_PAUSE", batch)
        self.assertIn("--skip-if-no-race-today", batch)
        self.assertIn("SKIP_IF_NO_RACE_TODAY", batch)
        self.assertIn("race_day_guard.py --date today", batch)
        self.assertIn("Skipping training, realtime result, odds, and trend report", batch)
        self.assertIn("(Get-Date).ToString('yyyyMMdd')", batch)
        self.assertIn("(Get-Date).AddDays(1).ToString('yyyyMMdd')", batch)
        self.assertIn("Result date rule: today", batch)
        self.assertNotIn("Result date rule: previous day", batch)
        self.assertIn("=== JV-Link result fetch ===", batch)
        self.assertNotIn("=== JV-Link previous day result fetch ===", batch)
        self.assertIn("scripts.fetch_odds --date %NEXT_DATE% --timeout-sec 3", batch)
        self.assertNotIn("RACE_DAY_EXIT", batch)
        guard_pos = batch.index("=== Race-day guard ===")
        training_pos = batch.index("=== JV-Link training fetch ===")
        self.assertLess(guard_pos, training_pos)
        self.assertLess(guard_pos, batch.index("=== JV-Link realtime result fetch ==="))
        self.assertLess(batch.index('cd /d "%JV_ROOT%"', guard_pos), training_pos)
        self.assertIn("reports\\logs", batch)
        self.assertIn("_sync_jvlink_then_collect.log", batch)
        self.assertIn('>> "%LOG_FILE%" echo Exit code: %EXIT_CODE%', batch)
        self.assertIn('type "%LOG_FILE%"', batch)
        self.assertIn('if "%NO_PAUSE%"=="0" pause', batch)
        self.assertIn("exit /b %EXIT_CODE%", batch)
        self.assertIn("--no-pause", task_script)
        self.assertIn("--skip-if-no-race-today", task_script)
        self.assertIn("keiba-trend-collect-raceday", task_script)
        self.assertIn("keiba-trend-collect-weekend", task_script)
        self.assertIn("Unregister-ScheduledTask", task_script)
        self.assertIn('New-ScheduledTaskTrigger -Daily -At "20:00"', task_script)
        self.assertIn("Schedule: Daily 20:00", task_script)
        self.assertNotIn("DaysOfWeek Saturday", task_script)
        self.assertIn("compare_previous_trends.py --pending-pairs", validation_batch)
        self.assertIn("--target-year %TARGET_YEAR%", validation_batch)
        self.assertIn("Target year: %TARGET_YEAR%", validation_batch)
        self.assertIn("summarize_trend_validation.py", validation_batch)
        self.assertIn("evaluate_recommendations.py", validation_batch)
        self.assertIn("--no-pause", validation_batch)
        self.assertIn("reports\\logs", validation_batch)
        self.assertIn("_weekly_validation_summary.log", validation_batch)
        self.assertIn('>> "%LOG_FILE%" echo Exit code: %EXIT_CODE%', validation_batch)
        self.assertIn('type "%LOG_FILE%"', validation_batch)
        self.assertIn('if "%NO_PAUSE%"=="0" pause', validation_batch)
        self.assertIn("exit /b %EXIT_CODE%", validation_batch)
        self.assertIn("run_weekly_validation_summary.bat", validation_task)
        self.assertIn("Monday", validation_task)
        self.assertIn('New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At "20:30"', validation_task)


if __name__ == "__main__":
    unittest.main()
