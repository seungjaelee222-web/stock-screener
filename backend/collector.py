# -*- coding: utf-8 -*-
"""
데이터 수집

핵심 변경 (ver0.1 대비)
  - 종목별 개별 조회 -> 일자별 전종목 조회
    ver0.1: 2,500종목 x 2회 = 약 5,000회 호출
    ver0.2: 거래일수 x 2개 시장. 5년 적재도 약 2,450회. 이후 하루 2회.
  - '오늘'이 아니라 '최근 영업일'을 지수 데이터로 역산한다.
    (공휴일 캘린더를 하드코딩하지 않는다)

주의
  일자별 전종목 조회는 수정주가가 반영되지 않을 수 있다.
  액면분할/병합이 있었던 종목은 이평선이 왜곡되므로 verify_splits()로
  점검하고, 해당 종목만 개별 조회로 다시 받는다.
"""
import os
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import datastore

# pykrx 1.2.x는 불러올 때 "KRX 로그인 실패: KRX_ID 또는 KRX_PW..." 를 출력한다.
#
# 이전 버전의 이 파일은 이 메시지를 "무시해도 되는 안내"로 보고 화면에서
# 숨겼는데, 잘못된 판단이었다. 로그인 없이 실행하면 지수·시가총액·영업일
# 조회가 전부 "Expecting value: line 1 column 1" (빈 응답) 오류로 실패하는
# 사례가 실제로 있었다 — 즉 KRX_ID/KRX_PW 로그인이 사실상 필요한 상황이
# 있다는 뜻이다. 그래서 메시지를 다시 보이게 하고, 아래 check_login()으로
# 실행 시작 시 한 번 더 명확하게 안내한다.
try:
    from pykrx import stock as krx
    PYKRX_AVAILABLE = True
except ImportError:
    PYKRX_AVAILABLE = False


def check_login() -> bool:
    """KRX_ID/KRX_PW 환경변수가 설정돼 있는지 확인하고, 없으면 안내를 출력한다.

    반환값은 "설정돼 있는지" 여부일 뿐이다. 설정이 없어도 수집을 막지는
    않는다 — 환경에 따라 로그인 없이도 되는 경우가 있어서다. 다만 최근
    실제로 로그인 없이는 모든 조회가 빈 응답으로 실패하는 사례가 확인됐으니,
    수집이 실패하면 가장 먼저 의심할 지점으로 이 안내를 보여준다.
    """
    has_login = bool(os.environ.get("KRX_ID") and os.environ.get("KRX_PW"))
    if not has_login:
        print("=" * 60)
        print("[안내] KRX 로그인 정보(KRX_ID, KRX_PW)가 설정되어 있지 않습니다.")
        print("       최근 KRX 서버는 로그인 없는 요청에 빈 응답만 주는")
        print("       경우가 있어, 이 상태로는 수집이 전부 실패할 수 있습니다.")
        print("       아래 순서로 해결해 보세요.")
        print("       1) https://data.krx.co.kr 에서 무료 회원가입")
        print("       2) 이 창에 아래 두 줄을 입력 (본인 계정으로 교체)")
        print('          set KRX_ID=본인아이디')
        print('          set KRX_PW=본인비밀번호')
        print("       3) 같은 창에서 python daily_scan.py --no-notify 다시 실행")
        print("=" * 60)
    return has_login

MARKETS = ["KOSPI", "KOSDAQ"]
INDEX_TICKER = {"KOSPI": "1001", "KOSDAQ": "2001"}
REQUEST_DELAY = 0.4          # KRX 차단 방지
SPLIT_JUMP_THRESHOLD = 0.30  # 하루 만에 30% 이상 벌어지면 분할 의심


def _require():
    if not PYKRX_AVAILABLE:
        raise RuntimeError("pykrx가 없습니다. 'pip install pykrx' 후 실행하세요.")


def _ymd(d) -> str:
    return d.strftime("%Y%m%d")


def latest_business_day(reference: datetime = None) -> str:
    """가장 최근 영업일을 반환한다.

    pykrx의 지수(index) 조회 API는 KRX 웹사이트 구조 변경에 취약해서 자주
    깨진다. get_nearest_business_day_in_a_week() 도 이름과 달리 내부에서
    결국 지수 조회를 거치길래(get_index_ohlcv_by_date) 믿을 수 없다고
    판단해 걷어냈다.

    대신 개별 종목(동화약품 000020)의 평범한 시세 조회 하나로 직접 확인한다.
    이 경로는 전종목 수집(fetch_day)이 매일 쓰는 것과 같은, 훨씬 덜 부서지는
    API다. 오늘부터 하루씩 거슬러 올라가며 데이터가 있는 첫날을 찾는다.
    """
    _require()
    ref = reference or datetime.now()
    probe = "000020"
    for i in range(15):
        d = ref - timedelta(days=i)
        ds = _ymd(d)
        try:
            df = krx.get_market_ohlcv(ds, ds, probe)
        except Exception:
            df = None
        if df is not None and not df.empty:
            return ds
        time.sleep(0.2)
    raise RuntimeError("최근 15일 이내 영업일을 확인하지 못했습니다. "
                        "KRX 사이트 접속 상태를 확인해 주세요.")


def business_days(fromdate: str, todate: str) -> list:
    """구간 내 영업일 목록 (YYYYMMDD 문자열 리스트)."""
    _require()
    days = krx.get_previous_business_days(fromdate=fromdate, todate=todate)
    return [pd.Timestamp(d).strftime("%Y%m%d") for d in days]


def fetch_day(date: str, market: str) -> pd.DataFrame:
    """특정 일자의 전종목 시세. 한 번의 호출로 시장 전체를 받는다."""
    _require()
    raw = krx.get_market_ohlcv(date, market=market)
    if raw is None or raw.empty:
        return pd.DataFrame()
    ren = {"종목명": "name", "시가": "open", "고가": "high", "저가": "low",
           "종가": "close", "거래량": "volume", "거래대금": "value"}
    raw = raw.rename(columns=ren)
    for col in ["name", "open", "high", "low", "close", "volume", "value"]:
        if col not in raw.columns:
            raw[col] = np.nan
    raw = raw[raw["close"] > 0]
    return raw[["name", "open", "high", "low", "close", "volume", "value"]]


def backfill(years: float = 5.0, progress_cb=None, upto: str = None) -> dict:
    """지정 기간의 일별 전종목 시세를 SQLite에 적재. 이미 있는 날짜는 건너뛴다."""
    _require()
    check_login()
    datastore.init_db()
    end = upto or latest_business_day()
    start = _ymd(datetime.strptime(end, "%Y%m%d") - timedelta(days=int(365 * years)))
    days = business_days(start, end)
    if not days:
        # 여기서 빈 리스트가 나오는 건 거의 항상 KRX 서버가 빈 응답을 준
        # 것이지, 진짜로 그 기간에 영업일이 없어서가 아니다. 이 상태로
        # 조용히 넘어가면 나중에 "적재된 시세가 없다"는 훨씬 헷갈리는
        # 오류로 이어지므로 여기서 바로 원인을 알려주고 멈춘다.
        raise RuntimeError(
            "영업일 목록을 하나도 받지 못했습니다. KRX 서버가 요청을 "
            "거부했을 가능성이 큽니다. 화면에 [안내]로 표시된 로그인 "
            "설정 방법을 먼저 확인해 주세요.")
    have = datastore.saved_dates()
    todo = [d for d in days if pd.Timestamp(d).strftime("%Y-%m-%d") not in have]

    total = len(todo) * len(MARKETS)
    done = 0
    for d in todo:
        iso = pd.Timestamp(d).strftime("%Y-%m-%d")
        for m in MARKETS:
            try:
                df = fetch_day(d, m)
                if not df.empty:
                    datastore.save_day(iso, df, m)
            except Exception as e:
                print(f"[warn] {d} {m} 수집 실패: {e}")
            done += 1
            if progress_cb:
                progress_cb(done, total)
            time.sleep(REQUEST_DELAY)

    # 지수도 함께 적재 (시장 국면 판정용)
    for name, tk in INDEX_TICKER.items():
        try:
            idx = krx.get_index_ohlcv(start, end, tk)
            idx = idx.rename(columns={"시가": "open", "고가": "high", "저가": "low",
                                      "종가": "close", "거래량": "volume"})
            for dt, row in idx.iterrows():
                datastore.save_index_day(pd.Timestamp(dt).strftime("%Y-%m-%d"), tk, row.to_dict())
        except Exception as e:
            print(f"[warn] 지수 {name} 적재 실패: {e}")

    return {"days_added": len(todo), "base_date": end}


def verify_splits(lookback_days: int = 5) -> list:
    """최근 며칠 사이 종가가 비정상적으로 튄 종목 목록.

    액면분할/병합 가능성이 있으므로 개별 조회(수정주가 반영)로 다시 받는다.
    """
    panel = datastore.load_panel()
    if panel.empty:
        return []
    suspects = []
    for code, g in panel.groupby("code"):
        g = g.sort_values("date").tail(lookback_days + 1)
        if len(g) < 2:
            continue
        r = g["close"].pct_change().abs().max()
        if pd.notna(r) and r > SPLIT_JUMP_THRESHOLD:
            suspects.append(code)
    return suspects


def refetch_stock(code: str, days: int = 400):
    """분할 의심 종목만 개별 조회(수정주가 반영)로 덮어쓴다."""
    _require()
    end = datetime.now()
    start = end - timedelta(days=int(days * 1.6))
    raw = krx.get_market_ohlcv(_ymd(start), _ymd(end), code)  # adjusted=True 기본
    raw = raw.rename(columns={"시가": "open", "고가": "high", "저가": "low",
                              "종가": "close", "거래량": "volume", "거래대금": "value"})
    name = krx.get_market_ticker_name(code)
    with datastore.conn() as c:
        for dt, row in raw.iterrows():
            iso = pd.Timestamp(dt).strftime("%Y-%m-%d")
            c.execute("UPDATE prices SET open=?,high=?,low=?,close=?,volume=? "
                      "WHERE date=? AND code=?",
                      (row.get("open"), row.get("high"), row.get("low"),
                       row.get("close"), row.get("volume"), iso, code))
    return name


def market_caps(date: str) -> dict:
    """시가총액 (제외 필터용). 실패해도 스캔은 계속 진행한다."""
    _require()
    out = {}
    for m in MARKETS:
        try:
            df = krx.get_market_cap(date, market=m)
            col = "시가총액" if "시가총액" in df.columns else df.columns[-1]
            for code, row in df.iterrows():
                out[str(code)] = float(row[col])
        except Exception as e:
            print(f"[warn] 시가총액 조회 실패 {m}: {e}")
    return out


# ---------------------------------------------------------------------------
# 데모 데이터 — 인터넷/pykrx 없이 전체 흐름을 확인하기 위한 것
# ---------------------------------------------------------------------------
def seed_demo(n_stocks: int = 120, days: int = 300, seed: int = 7):
    """합성 시세를 SQLite에 넣어 수집 이후 단계를 그대로 돌려볼 수 있게 한다."""
    import random
    datastore.init_db()
    rnd = random.Random(seed)
    base = datetime.now()
    dates, d = [], base
    while len(dates) < days:
        if d.weekday() < 5:
            dates.append(d)
        d -= timedelta(days=1)
    dates = sorted(dates)

    names = ["데모전자", "데모바이오", "데모케미칼", "데모조선", "데모반도체",
             "데모철강", "데모유통", "데모에너지", "데모소재", "데모플랫폼"]
    for i in range(n_stocks):
        code = "D%05d" % i
        name = f"{names[i % len(names)]}{i//len(names)+1}"
        r = random.Random(code)
        # 종목마다 추세 성향을 다르게 준다 (상승/횡보/하락)
        drift = r.choice([0.0022, 0.0012, 0.0004, -0.0004, -0.0015])
        price = 3000 + r.random() * 60000
        rows = []
        for dt in dates:
            price = max(300, price * (1 + drift + r.uniform(-0.022, 0.024)))
            o = price * (1 + r.uniform(-0.007, 0.007))
            c = price
            h = max(o, c) * (1 + r.uniform(0, 0.012))
            lo = min(o, c) * (1 - r.uniform(0, 0.012))
            vol = abs(r.gauss(400000, 120000))
            if r.random() < 0.05:
                vol *= r.uniform(2.0, 4.0)
            rows.append({"name": name, "open": o, "high": h, "low": lo,
                         "close": c, "volume": vol, "value": c * vol})
        df = pd.DataFrame(rows, index=[code] * len(rows))
        for dt, (_, row) in zip(dates, df.iterrows()):
            pass
        # 날짜별로 나눠 저장
        for dt, row in zip(dates, rows):
            one = pd.DataFrame([row], index=[code])
            datastore.save_day(dt.strftime("%Y-%m-%d"), one,
                               "KOSPI" if i % 2 == 0 else "KOSDAQ")

    # 지수도 합성
    r = random.Random("index")
    lvl = 2500.0
    for dt in dates:
        lvl = lvl * (1 + 0.0008 + r.uniform(-0.009, 0.0095))
        datastore.save_index_day(dt.strftime("%Y-%m-%d"), INDEX_TICKER["KOSPI"],
                                 {"open": lvl, "high": lvl * 1.003, "low": lvl * 0.997,
                                  "close": lvl, "volume": 5e8})
    return dates[-1].strftime("%Y-%m-%d")
