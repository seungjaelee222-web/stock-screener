# -*- coding: utf-8 -*-
"""
백테스트

목적
  기준값을 조정하기 전에, 지금 규칙이 시장 평균보다 나은지부터 확인한다.
  "고친 규칙이 나아진 것인지" 판단할 근거를 만드는 것이 이 파일의 유일한 역할이다.

설계
  - 분할 검증 : 앞 60% 구간에서 기준을 조정하고, 뒤 40%로만 검증한다.
                전 구간으로 최적화하면 그 구간에만 맞는 규칙이 된다.
  - 대조군    : 같은 날 전체 종목의 같은 기간 수익률.
                신호의 수익률이 아니라 '대조군 대비 초과분'을 본다.
  - 생존편향  : 상장폐지/거래정지 종목을 빼지 않는다. 데이터가 끊기면
                마지막 종가까지의 수익률로 반영한다.
  - 국면별    : 전체 평균은 하락장의 손실을 가린다. 지수 정배열 여부로 나눠 본다.

실행
  python backtest.py --horizons 5 10 20 60
"""
import argparse
import json
from collections import defaultdict

import numpy as np
import pandas as pd

import datastore
import screener


def forward_return(closes: pd.Series, i: int, h: int) -> float:
    """i일 종가 대비 i+h일 종가 수익률(%). 데이터가 끊기면 마지막 값 기준."""
    if i >= len(closes) - 1:
        return float("nan")
    j = min(i + h, len(closes) - 1)
    a, b = closes.iloc[i], closes.iloc[j]
    if not np.isfinite(a) or a <= 0:
        return float("nan")
    return (b - a) / a * 100.0


def build_signals(horizons, min_history=140):
    """전 종목 전 기간을 훑어 '신규 편입 조건'이 성립한 날을 모두 모은다."""
    panel = datastore.load_panel()
    if panel.empty:
        raise RuntimeError("적재된 시세가 없습니다. 먼저 수집하세요.")

    idx = datastore.load_index("1001")
    regime_by_date = {}
    if not idx.empty:
        ind = screener.compute_indicators(idx)
        for _, r in ind.iterrows():
            regime_by_date[str(r["date"])[:10]] = bool(r["aligned"]) if pd.notna(r["aligned"]) else False

    signals, baseline = [], defaultdict(list)

    for code, g in panel.groupby("code"):
        g = g.sort_values("date").reset_index(drop=True)
        if len(g) < min_history + max(horizons):
            continue
        name = g["name"].iloc[-1]
        if screener.is_excluded_name(name):
            continue
        d = screener.compute_indicators(g)
        closes = d["close"]

        # 대조군: 모든 종목/모든 날의 미래 수익률
        for i in range(min_history, len(d) - 1):
            date = str(d["date"].iloc[i])[:10]
            for h in horizons:
                r = forward_return(closes, i, h)
                if np.isfinite(r):
                    baseline[h].append(r)

        # 신호일 탐색: 어제는 정배열이 아니었고 오늘 정배열이 된 날
        for i in range(min_history, len(d) - 1):
            if not bool(d["aligned"].iloc[i]):
                continue
            window = d.iloc[: i + 1]
            if screener.count_align_days(window) > screener.CONFIG["entry_max_align_days"]:
                continue
            if screener.check_exit(window):
                continue
            q = screener.quality_score(window)
            if q["score"] < screener.CONFIG["entry_min_score"]:
                continue
            if q["disparity"] is not None and q["disparity"] > screener.CONFIG["entry_max_disparity"]:
                continue

            date = str(d["date"].iloc[i])[:10]
            rec = {"code": code, "name": name, "date": date, "score": q["score"],
                   "align_days": q["align_days"],
                   "regime_aligned": regime_by_date.get(date)}
            for h in horizons:
                rec[f"r{h}"] = forward_return(closes, i, h)
            signals.append(rec)

    return pd.DataFrame(signals), {h: np.array(v) for h, v in baseline.items()}


def report(sig: pd.DataFrame, base: dict, horizons, split=0.6):
    if sig.empty:
        return "신호가 하나도 발생하지 않았습니다. 편입 조건이 지나치게 좁습니다."

    sig = sig.sort_values("date").reset_index(drop=True)
    cut = sig["date"].quantile(split, interpolation="nearest") if len(sig) > 10 else None
    out = []
    out.append(f"신호 {len(sig)}건 · 기간 {sig['date'].min()} ~ {sig['date'].max()}")
    if cut:
        out.append(f"학습 구간 ~{cut} / 검증 구간 {cut}~  (검증 구간 결과만 신뢰할 것)")
    out.append("")

    def block(title, df):
        if df.empty:
            return
        out.append(f"[{title}]  {len(df)}건")
        out.append("  기간   신호중앙값   대조군중앙값   초과분   승률")
        for h in horizons:
            col = f"r{h}"
            v = df[col].dropna().values
            if len(v) == 0:
                continue
            b = base.get(h, np.array([]))
            bm = float(np.median(b)) if len(b) else float("nan")
            out.append(f"  {h:>3}일  {np.median(v):>9.2f}%  {bm:>11.2f}%  "
                       f"{np.median(v)-bm:>+7.2f}%p  {(v>0).mean()*100:>5.1f}%")
        out.append("")

    block("전체", sig)
    if cut:
        block("검증 구간(out-of-sample)", sig[sig["date"] > cut])
    if sig["regime_aligned"].notna().any():
        block("지수 정배열 구간", sig[sig["regime_aligned"] == True])
        block("지수 비정배열 구간", sig[sig["regime_aligned"] == False])

    out.append("판정 기준")
    out.append("  검증 구간 20일 초과분이 0 근처거나 음수면, 기준값을 조정할 것이 아니라")
    out.append("  조건 자체를 바꿔야 합니다. 지수 비정배열 구간에서만 무너진다면")
    out.append("  시장 국면 필터를 편입 조건에 넣으십시오.")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizons", type=int, nargs="+", default=[5, 10, 20, 60])
    ap.add_argument("--split", type=float, default=0.6)
    ap.add_argument("--save", default="backtest_result.csv")
    args = ap.parse_args()

    datastore.init_db()
    sig, base = build_signals(args.horizons)
    print(report(sig, base, args.horizons, args.split))
    if not sig.empty:
        sig.to_csv(args.save, index=False, encoding="utf-8-sig")
        print(f"\n신호 원자료 저장: {args.save}")


if __name__ == "__main__":
    main()
