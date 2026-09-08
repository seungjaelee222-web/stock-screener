# -*- coding: utf-8 -*-
"""
정배열 스크리너 판정 로직 (ver0.2)

설계 원칙
  1) 제외 필터는 많이, 순위 가중치는 적게(4개)  -> 과최적화 방지
  2) 편입(NEW)/유지(HOLD)/편출(DROP)을 매일 판정  -> 조건 이탈 시 즉시 제거
  3) 이 모듈은 순수 함수만 담는다. 데이터 수집/저장은 collector.py, datastore.py

이 파일에는 네트워크 호출이 없다. 따라서 단위 테스트가 가능하다.
"""
import numpy as np
import pandas as pd

MA_WINDOWS = [5, 20, 60, 120]

# ---------------------------------------------------------------------------
# 설정값
#   주의: 아래 수치는 백테스트로 검증되기 전의 초기값이다.
#         backtest.py 결과를 보고 조정한 뒤 고정할 것.
# ---------------------------------------------------------------------------
CONFIG = {
    # --- 1층: 제외 필터 (판단 없이 기계적으로 쳐냄) ---
    "min_value_ma20": 5_00_000_000,   # 20일 평균 거래대금 5억 원
    "min_market_cap": 50_000_000_000, # 시가총액 500억 원 (없으면 검사 생략)
    "max_surge_20d": 50.0,            # 최근 20거래일 상승률 상한 (추격 방지)
    "min_history_days": 140,          # MA120 + 여유

    # --- 2층: 정배열 유지 조건 (하나라도 깨지면 즉시 편출) ---
    "exit_disparity_ma20": 12.0,      # 20일선 이격도 상한 (과열 -> 관찰 종료)
    "ma120_slope_window": 20,         # MA120 기울기 측정 구간
    "ma20_slope_window": 5,

    # --- 3층: 신규 편입 시에만 적용하는 추가 조건 ---
    "entry_max_align_days": 15,       # 정배열 15일차 이내에만 신규 편입
    "entry_max_disparity": 8.0,       # 신규 편입 시 이격도 상한
    "entry_min_score": 55,            # 신규 편입 최저 품질 점수
}

DROP_REASONS = {
    "ORDER": "이평선 정배열 붕괴",
    "BELOW_MA20": "종가가 20일선 아래",
    "MA120_DOWN": "120일선 하락 전환",
    "LIQUIDITY": "거래대금 기준 미달",
    "OVERHEAT": "20일선 이격도 과열",
    "DATA": "데이터 부족",
}


# ---------------------------------------------------------------------------
# 지표 계산
# ---------------------------------------------------------------------------
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """df 컬럼: date, open, high, low, close, volume, value(거래대금, 선택)"""
    df = df.copy().sort_values("date").reset_index(drop=True)
    for w in MA_WINDOWS:
        df[f"ma{w}"] = df["close"].rolling(w).mean()
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    if "value" not in df.columns:
        df["value"] = df["close"] * df["volume"]
    df["value_ma20"] = df["value"].rolling(20).mean()
    df["value_ma60"] = df["value"].rolling(60).mean()
    df["aligned"] = (
        (df["ma5"] > df["ma20"])
        & (df["ma20"] > df["ma60"])
        & (df["ma60"] > df["ma120"])
        & (df["close"] > df["ma20"])
    )
    return df


def slope_pct(series: pd.Series, window: int) -> float:
    """window 거래일 전 대비 변화율(%). 계산 불가 시 nan."""
    if len(series) <= window:
        return float("nan")
    past, now = series.iloc[-1 - window], series.iloc[-1]
    if not np.isfinite(past) or not np.isfinite(now) or past == 0:
        return float("nan")
    return (now - past) / abs(past) * 100.0


def count_align_days(df: pd.DataFrame) -> int:
    """마지막 행 기준으로 정배열이 며칠째 연속 유지 중인지."""
    if df.empty or not bool(df["aligned"].iloc[-1]):
        return 0
    n = 0
    for v in reversed(df["aligned"].tolist()):
        if bool(v):
            n += 1
        else:
            break
    return n


# ---------------------------------------------------------------------------
# 유지 조건 판정 (편출 규칙) — 사용자 요구의 핵심
# ---------------------------------------------------------------------------
def check_exit(df: pd.DataFrame, cfg: dict = None) -> list:
    """유지 조건을 어긴 사유 코드 목록을 반환. 빈 리스트면 계속 보유(관찰) 대상."""
    cfg = cfg or CONFIG
    reasons = []

    if len(df) < cfg["min_history_days"] or not np.isfinite(df["ma120"].iloc[-1]):
        return ["DATA"]

    last = df.iloc[-1]
    ma5, ma20, ma60, ma120 = last["ma5"], last["ma20"], last["ma60"], last["ma120"]

    if not (ma5 > ma20 > ma60 > ma120):
        reasons.append("ORDER")
    if not (last["close"] > ma20):
        reasons.append("BELOW_MA20")

    ma120_slope = slope_pct(df["ma120"], cfg["ma120_slope_window"])
    if np.isfinite(ma120_slope) and ma120_slope <= 0:
        reasons.append("MA120_DOWN")

    value_ma20 = last.get("value_ma20", float("nan"))
    if np.isfinite(value_ma20) and value_ma20 < cfg["min_value_ma20"]:
        reasons.append("LIQUIDITY")

    if ma20 > 0:
        disparity = (last["close"] - ma20) / ma20 * 100.0
        if disparity > cfg["exit_disparity_ma20"]:
            reasons.append("OVERHEAT")

    return reasons


# ---------------------------------------------------------------------------
# 제외 필터 (종목 자체의 성격)
# ---------------------------------------------------------------------------
def is_excluded_name(name: str) -> bool:
    """우선주 / 스팩 / 리츠 등 판정 대상에서 뺄 종목명 패턴."""
    if not name:
        return True
    n = name.strip()
    if "스팩" in n:
        return True
    if n.endswith("우") or n.endswith("우B") or n.endswith("우C"):
        return True
    for p in ("2우", "3우", "4우"):
        if p in n:
            return True
    if "리츠" in n:
        return True
    return False


def surge_20d(df: pd.DataFrame) -> float:
    if len(df) < 21:
        return float("nan")
    past, now = df["close"].iloc[-21], df["close"].iloc[-1]
    if past == 0:
        return float("nan")
    return (now - past) / past * 100.0


def has_trading_halt(df: pd.DataFrame, lookback: int = 20) -> bool:
    """최근 lookback 거래일 중 거래량 0인 날이 있으면 거래정지 의심."""
    if len(df) < lookback:
        return True
    return bool((df["volume"].iloc[-lookback:] <= 0).any())


# ---------------------------------------------------------------------------
# 품질 점수 (0~100) — 순위용. 지표는 4개만 쓴다.
# ---------------------------------------------------------------------------
def quality_score(df: pd.DataFrame, cfg: dict = None) -> dict:
    cfg = cfg or CONFIG
    last = df.iloc[-1]
    detail = {}

    # (1) 정배열 초입일수록 가점 — 40점
    align_days = count_align_days(df)
    if align_days <= 0:
        s_align = 0
    elif align_days <= 5:
        s_align = 40
    elif align_days <= 10:
        s_align = 34
    elif align_days <= 20:
        s_align = 26
    elif align_days <= 40:
        s_align = 16
    else:
        s_align = 8
    detail["정배열 초입"] = s_align

    # (2) 120일선 기울기 — 25점 (장기추세가 실제 상승인지)
    ma120_slope = slope_pct(df["ma120"], cfg["ma120_slope_window"])
    if not np.isfinite(ma120_slope) or ma120_slope <= 0:
        s_slope = 0
    else:
        s_slope = int(min(25, ma120_slope / 4.0 * 25))
    detail["120일선 기울기"] = s_slope

    # (3) 20일선 이격도 위치 — 25점 (과열 회피)
    ma20 = last["ma20"]
    disparity = (last["close"] - ma20) / ma20 * 100.0 if ma20 else float("nan")
    if not np.isfinite(disparity):
        s_gap = 0
    elif disparity <= 5:
        s_gap = 25
    elif disparity <= 8:
        s_gap = 16
    elif disparity <= 12:
        s_gap = 8
    else:
        s_gap = 0
    detail["이격도 위치"] = s_gap

    # (4) 거래대금 증가 추세 — 10점
    v20, v60 = last.get("value_ma20"), last.get("value_ma60")
    ratio = (v20 / v60) if (v20 and v60 and np.isfinite(v20) and np.isfinite(v60) and v60 > 0) else float("nan")
    if not np.isfinite(ratio) or ratio < 1.0:
        s_val = 0
    elif ratio >= 1.5:
        s_val = 10
    elif ratio >= 1.2:
        s_val = 7
    else:
        s_val = 4
    detail["거래대금 증가"] = s_val

    return {
        "score": int(s_align + s_slope + s_gap + s_val),
        "detail": detail,
        "align_days": int(align_days),
        "ma120_slope": None if not np.isfinite(ma120_slope) else round(float(ma120_slope), 2),
        "disparity": None if not np.isfinite(disparity) else round(float(disparity), 2),
        "value_trend": None if not np.isfinite(ratio) else round(float(ratio), 2),
    }


# ---------------------------------------------------------------------------
# 종목 1개 종합 평가
# ---------------------------------------------------------------------------
def evaluate(df: pd.DataFrame, name: str = "", market_cap: float = None,
             is_incumbent: bool = False, cfg: dict = None) -> dict:
    """
    is_incumbent : 어제까지 리스트에 있던 종목인지 여부.
                   기존 종목은 '유지 조건'만 보고, 신규 종목은 '편입 조건'까지 본다.
                   -> 편입은 까다롭게, 유지는 명확하게. 한 번 들어온 종목이
                      편입 조건(초입일수 등) 때문에 튕겨 나가지 않도록 분리한 것.
    반환 state: NEW | HOLD | DROP | NONE
    """
    cfg = cfg or CONFIG

    if is_excluded_name(name):
        return {"state": "NONE", "reasons": ["종목 유형 제외"], "score": 0}
    if len(df) < cfg["min_history_days"]:
        return {"state": "DROP" if is_incumbent else "NONE",
                "reasons": [DROP_REASONS["DATA"]], "score": 0}
    if has_trading_halt(df):
        return {"state": "DROP" if is_incumbent else "NONE",
                "reasons": ["최근 거래정지 의심"], "score": 0}

    exit_codes = check_exit(df, cfg)
    if exit_codes:
        return {"state": "DROP" if is_incumbent else "NONE",
                "reasons": [DROP_REASONS.get(c, c) for c in exit_codes],
                "reason_codes": exit_codes, "score": 0}

    q = quality_score(df, cfg)
    last = df.iloc[-1]
    prev_close = df["close"].iloc[-2]
    base = {
        "score": q["score"],
        "score_detail": q["detail"],
        "align_days": q["align_days"],
        "disparity": q["disparity"],
        "ma120_slope": q["ma120_slope"],
        "value_trend": q["value_trend"],
        "close": float(last["close"]),
        "change_pct": round(float((last["close"] - prev_close) / prev_close * 100.0), 2)
        if prev_close else 0.0,
        "value_ma20": float(last.get("value_ma20") or 0),
        "surge_20d": None if not np.isfinite(surge_20d(df)) else round(float(surge_20d(df)), 1),
    }

    if is_incumbent:
        base.update({"state": "HOLD", "reasons": []})
        return base

    # --- 신규 편입 조건 (여기서만 까다롭게) ---
    fails = []
    if market_cap is not None and market_cap < cfg["min_market_cap"]:
        fails.append("시가총액 기준 미달")
    if q["align_days"] > cfg["entry_max_align_days"]:
        fails.append(f"정배열 {q['align_days']}일차(초입 아님)")
    if q["disparity"] is not None and q["disparity"] > cfg["entry_max_disparity"]:
        fails.append(f"이격도 {q['disparity']}%")
    s20 = surge_20d(df)
    if np.isfinite(s20) and s20 > cfg["max_surge_20d"]:
        fails.append(f"20일 급등 {s20:.0f}%")
    if q["score"] < cfg["entry_min_score"]:
        fails.append(f"품질점수 {q['score']}점")

    if fails:
        base.update({"state": "NONE", "reasons": fails})
        return base

    base.update({"state": "NEW", "reasons": []})
    return base


# ---------------------------------------------------------------------------
# 시장 국면
# ---------------------------------------------------------------------------
def market_regime(index_df: pd.DataFrame) -> dict:
    """지수 일봉으로 시장 자체의 정배열 여부를 판정."""
    if index_df is None or len(index_df) < 121:
        return {"aligned": None, "label": "판정 불가", "align_days": 0}
    d = compute_indicators(index_df)
    aligned = bool(d["aligned"].iloc[-1])
    days = count_align_days(d)
    slope = slope_pct(d["ma120"], 20)
    if aligned and days >= 5:
        label = "정배열 (개별 종목 신뢰도 양호)"
    elif aligned:
        label = "정배열 진입 직후"
    elif np.isfinite(slope) and slope > 0:
        label = "정배열 아님 / 장기추세는 상승 (주의)"
    else:
        label = "역배열 (신규 진입 자제 권고)"
    return {"aligned": aligned, "label": label, "align_days": int(days),
            "ma120_slope": None if not np.isfinite(slope) else round(float(slope), 2)}


def to_chart_json(df: pd.DataFrame) -> dict:
    candles, volumes = [], []
    ma_series = {f"ma{w}": [] for w in MA_WINDOWS}
    for _, row in df.iterrows():
        t = pd.Timestamp(row["date"]).strftime("%Y-%m-%d")
        candles.append({"time": t, "open": float(row["open"]), "high": float(row["high"]),
                        "low": float(row["low"]), "close": float(row["close"])})
        up = row["close"] >= row["open"]
        volumes.append({"time": t, "value": float(row["volume"]),
                        "color": "rgba(229,72,77,0.5)" if up else "rgba(76,139,245,0.5)"})
        for w in MA_WINDOWS:
            val = row.get(f"ma{w}")
            if pd.notna(val):
                ma_series[f"ma{w}"].append({"time": t, "value": float(val)})
    return {"candles": candles, "volumes": volumes, "ma": ma_series}
