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


if __name__ == "__main__":
    unittest.main(verbosity=2)
