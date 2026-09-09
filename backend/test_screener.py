# -*- coding: utf-8 -*-
"""판정 로직 단위 테스트.  실행:  python -m unittest test_screener -v"""
import unittest
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import screener


def make_series(closes, volume=1_000_000, value=None):
    n = len(closes)
    dates = [datetime(2025, 1, 1) + timedelta(days=i) for i in range(n)]
    return pd.DataFrame({
        "date": dates,
        "open": closes, "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes], "close": closes,
        "volume": [volume] * n,
        "value": [value if value else c * volume for c in closes],
    })


def rising(n=200, start=10000, rate=0.004):
    return [start * (1 + rate) ** i for i in range(n)]


def falling(n=200, start=30000, rate=-0.004):
    return [start * (1 + rate) ** i for i in range(n)]


class TestAlignment(unittest.TestCase):
    def test_rising_is_aligned(self):
        d = screener.compute_indicators(make_series(rising()))
        self.assertTrue(bool(d["aligned"].iloc[-1]))
        self.assertEqual(screener.check_exit(d), [])

    def test_falling_is_not_aligned(self):
        d = screener.compute_indicators(make_series(falling()))
        self.assertFalse(bool(d["aligned"].iloc[-1]))
        self.assertIn("ORDER", screener.check_exit(d))

    def test_align_days_counts_streak(self):
        # 앞 절반 하락, 뒤 절반 상승 -> 정배열 일수가 유한하게 잡혀야 한다
        closes = falling(120, 30000) + rising(120, 20000, 0.006)
        d = screener.compute_indicators(make_series(closes))
        days = screener.count_align_days(d)
        self.assertGreater(days, 0)
        self.assertLess(days, 120)


class TestExitRules(unittest.TestCase):
    """조건 이탈 시 가차없이 편출되는지 — 이 도구의 핵심 요구사항."""

    def test_drop_when_order_breaks(self):
        closes = rising(190) + [rising(190)[-1] * 0.80] * 10  # 급락으로 순서 붕괴
        d = screener.compute_indicators(make_series(closes))
        res = screener.evaluate(d, name="테스트", is_incumbent=True)
        self.assertEqual(res["state"], "DROP")

    def test_drop_when_below_ma20(self):
        base = rising(195)
        closes = base + [base[-1] * 0.93] * 5
        d = screener.compute_indicators(make_series(closes))
        codes = screener.check_exit(d)
        self.assertTrue("BELOW_MA20" in codes or "ORDER" in codes)

    def test_drop_on_low_liquidity(self):
        d = screener.compute_indicators(make_series(rising(), volume=10, value=1_000_000))
        self.assertIn("LIQUIDITY", screener.check_exit(d))

    def test_drop_on_overheat(self):
        base = rising(195)
        closes = base + [base[-1] * 1.30] * 5   # 20일선에서 크게 벌어짐
        d = screener.compute_indicators(make_series(closes))
        self.assertIn("OVERHEAT", screener.check_exit(d))

    def test_incumbent_drops_but_stranger_is_none(self):
        d = screener.compute_indicators(make_series(falling()))
        self.assertEqual(screener.evaluate(d, name="A", is_incumbent=True)["state"], "DROP")
        self.assertEqual(screener.evaluate(d, name="A", is_incumbent=False)["state"], "NONE")


class TestEntryRules(unittest.TestCase):
    def test_late_alignment_not_entered(self):
        """정배열 100일차 종목은 신규 편입되지 않아야 한다."""
        d = screener.compute_indicators(make_series(rising(300)))
        res = screener.evaluate(d, name="테스트", is_incumbent=False)
        self.assertEqual(res["state"], "NONE")
        self.assertTrue(any("초입" in r for r in res["reasons"]))

    def test_incumbent_keeps_hold_even_if_late(self):
        """이미 들어온 종목은 초입이 아니라는 이유로 쫓아내지 않는다."""
        d = screener.compute_indicators(make_series(rising(300)))
        res = screener.evaluate(d, name="테스트", is_incumbent=True)
        self.assertEqual(res["state"], "HOLD")


class TestFilters(unittest.TestCase):
    def test_excluded_names(self):
        for n in ["삼성전자우", "미래에셋비전스팩1호", "케이비2우B", "이지스밸류리츠"]:
            self.assertTrue(screener.is_excluded_name(n), n)
        for n in ["삼성전자", "카카오", "우리기술"]:
            self.assertFalse(screener.is_excluded_name(n), n)

    def test_short_history_is_none(self):
        d = screener.compute_indicators(make_series(rising(80)))
        self.assertEqual(screener.evaluate(d, name="신규주")["state"], "NONE")

    def test_halt_detection(self):
        closes = rising(200)
        df = make_series(closes)
        df.loc[df.index[-3], "volume"] = 0
        d = screener.compute_indicators(df)
        self.assertTrue(screener.has_trading_halt(d))


class TestScore(unittest.TestCase):
    def test_score_range(self):
        d = screener.compute_indicators(make_series(rising()))
        q = screener.quality_score(d)
        self.assertGreaterEqual(q["score"], 0)
        self.assertLessEqual(q["score"], 100)

    def test_early_beats_late(self):
        early = screener.compute_indicators(make_series(falling(140, 30000) + rising(60, 20000, 0.008)))
        late = screener.compute_indicators(make_series(rising(400)))
        self.assertGreater(screener.quality_score(early)["detail"]["정배열 초입"],
                           screener.quality_score(late)["detail"]["정배열 초입"])
        # 총점은 합성 데이터 특성상 역전될 수 있으나, 편입은 align_days 게이트가
        # 별도로 막으므로 후기 종목이 새로 들어오는 일은 없다.
        self.assertEqual(screener.evaluate(late, name="후기")["state"], "NONE")


class TestRobustness(unittest.TestCase):
    def test_zero_price_does_not_crash(self):
        closes = rising(200)
        closes[-1] = 0.0001
        d = screener.compute_indicators(make_series(closes))
        screener.check_exit(d)
        screener.evaluate(d, name="테스트", is_incumbent=True)

    def test_nan_ma120(self):
        d = screener.compute_indicators(make_series(rising(50)))
        self.assertEqual(screener.check_exit(d), ["DATA"])


class TestWatchCandidate(unittest.TestCase):
    """관찰 후보(참고용, 정배열 아님) — 거래량 급증 + 20일선 근접만 본다."""

    def test_volume_surge_near_ma20_passes(self):
        base = rising(190, start=10000, rate=0.001)  # 완만한 흐름, 정배열 여부 무관
        closes = base[:-1] + [base[-2] * 1.001]       # 마지막 날 20일선 근접 유지
        df = make_series(closes, volume=100_000)
        df.loc[df.index[-1], "volume"] = 500_000       # 마지막 날 거래량 5배 급증
        d = screener.compute_indicators(df)
        w = screener.watch_candidate(d)
        self.assertTrue(w["passes"])
        self.assertGreaterEqual(w["vol_ratio"], screener.CONFIG["watch_vol_ratio"])

    def test_no_volume_surge_fails(self):
        d = screener.compute_indicators(make_series(rising(190, rate=0.001), volume=100_000))
        self.assertFalse(screener.watch_candidate(d)["passes"])

    def test_far_from_ma20_fails_even_with_volume(self):
        base = rising(190, rate=0.001)
        closes = base[:-1] + [base[-2] * 1.20]  # 20일선에서 크게 벌어짐
        df = make_series(closes, volume=100_000)
        df.loc[df.index[-1], "volume"] = 500_000
        d = screener.compute_indicators(df)
        self.assertFalse(screener.watch_candidate(d)["passes"])

    def test_short_history_fails(self):
        d = screener.compute_indicators(make_series(rising(20), volume=100_000))
        self.assertFalse(screener.watch_candidate(d)["passes"])

    def test_low_liquidity_fails(self):
        base = rising(190, start=10000, rate=0.001)
        closes = base[:-1] + [base[-2] * 1.001]
        df = make_series(closes, volume=10)  # 거래대금 자체가 너무 작음
        df.loc[df.index[-1], "volume"] = 10 * 5
        d = screener.compute_indicators(df)
        self.assertFalse(screener.watch_candidate(d)["passes"])

    def test_does_not_require_alignment(self):
        """정배열이 아니어도(하락 추세여도) 신호가 뜰 수 있어야 한다 — 이게 목적."""
        base = falling(190, start=30000, rate=-0.001)
        closes = base[:-1] + [base[-2] * 1.005]
        df = make_series(closes, volume=100_000)
        df.loc[df.index[-1], "volume"] = 500_000
        d = screener.compute_indicators(df)
        self.assertFalse(bool(d["aligned"].iloc[-1]))  # 정배열 아님을 확인
        w = screener.watch_candidate(d)
        self.assertTrue(w["passes"])  # 그래도 관찰 후보 신호는 뜬다

    def test_new_hold_stocks_excluded_at_pipeline_level(self):
        """watch_candidate 자체는 순수 함수라 정배열 여부를 안 본다.
        NEW/HOLD와 중복 노출을 막는 건 pipeline.run_scan()의 책임이다 —
        이 테스트는 그 분리 설계를 문서로 남겨두는 용도."""
        base = rising(190, start=10000, rate=0.004)  # 정배열 + 초입
        df = make_series(base, volume=100_000)
        df.loc[df.index[-1], "volume"] = 500_000
        d = screener.compute_indicators(df)
        ev = screener.evaluate(d, name="테스트", is_incumbent=False)
        w = screener.watch_candidate(d)
        # 둘 다 참일 수 있다 — 실제 중복 제거는 pipeline.py에서 상태를 보고 거른다.
        self.assertIn(ev["state"], ("NEW", "NONE"))
        self.assertIsInstance(w["passes"], bool)


class TestStage6Signal(unittest.TestCase):
    """일본 대순환분석의 '스테이지6'(단기>장기>중기) — 5/20/60일선으로 이식."""

    def test_fresh_crossover_passes(self):
        # 100일 완만한 하락 후 마지막 3일만 급등 -> 5일선이 빠르게 60일선을
        # 추월하지만 20일선은 아직 못 따라온 상태 (스테이지6 진입 순간).
        closes = falling(100, start=20000, rate=-0.0015)
        last_close = closes[-1]
        jump = [last_close * 1.04, last_close * 1.04 * 1.045,
                last_close * 1.04 * 1.045 * 1.05]
        closes = closes + jump
        df = make_series(closes, volume=1_000_000)
        d = screener.compute_indicators(df)
        last, prev = d.iloc[-1], d.iloc[-2]
        # 시나리오가 의도대로 만들어졌는지 먼저 확인 (아니면 테스트가 무의미)
        self.assertTrue(last["ma5"] > last["ma60"] and last["ma20"] < last["ma60"],
                        "오늘 스테이지6 상태가 되도록 시나리오를 만들지 못함")
        self.assertFalse(prev["ma5"] > prev["ma60"],
                         "어제 이미 스테이지6이면 '신선한 진입'이 아니라 테스트 무의미")
        w = screener.stage6_signal(d)
        self.assertTrue(w["passes"])

    def test_not_fresh_fails(self):
        """어제도 이미 같은 상태였다면(신선하지 않으면) 신호 없음."""
        closes = rising(120, start=10000, rate=0.01)  # 오래 전에 이미 5>60 상태
        df = make_series(closes, volume=1_000_000)
        d = screener.compute_indicators(df)
        w = screener.stage6_signal(d)
        self.assertFalse(w["passes"])

    def test_full_alignment_is_not_stage6(self):
        """20일선까지 60일선을 넘으면(완전 정배열) 스테이지6이 아니라 별개다."""
        closes = rising(190, start=10000, rate=0.004)
        df = make_series(closes, volume=1_000_000)
        d = screener.compute_indicators(df)
        self.assertTrue(bool(d["aligned"].iloc[-1]))  # 완전 정배열 확인
        w = screener.stage6_signal(d)
        self.assertFalse(w["passes"])

    def test_short_history_fails(self):
        d = screener.compute_indicators(make_series(rising(40), volume=1_000_000))
        self.assertFalse(screener.stage6_signal(d)["passes"])


class TestEvaluateWatch(unittest.TestCase):
    """두 관찰 신호(거래량/스테이지6)를 하나로 모으는 evaluate_watch()."""

    def test_volume_only_reports_source(self):
        base = rising(190, start=10000, rate=0.001)
        closes = base[:-1] + [base[-2] * 1.001]
        df = make_series(closes, volume=100_000)
        df.loc[df.index[-1], "volume"] = 500_000
        d = screener.compute_indicators(df)
        w = screener.evaluate_watch(d)
        self.assertTrue(w["passes"])
        self.assertIn("거래량", w["sources"])
        self.assertNotIn("스테이지6", w["sources"])

    def test_neither_signal_fails(self):
        d = screener.compute_indicators(make_series(rising(190, rate=0.001), volume=100_000))
        w = screener.evaluate_watch(d)
        self.assertFalse(w["passes"])
        self.assertEqual(w["sources"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
