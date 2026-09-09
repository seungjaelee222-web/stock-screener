# -*- coding: utf-8 -*-
"""
로컬 저장소 (SQLite)

  prices        : 일자별 전종목 시세 캐시. 한 번 받으면 다시 받지 않는다.
  scan_runs     : 스캔 실행 이력 + 시장 국면
  positions     : 관찰 리스트. 편입일 / 편출일 / 편출 사유를 기록한다.
  scan_results  : 스캔 시점의 종목별 스냅샷 (편입/유지/편출 상태 포함)
"""
import json
import os
import sqlite3
from contextlib import contextmanager

import pandas as pd

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "screener.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    date TEXT NOT NULL, code TEXT NOT NULL, market TEXT, name TEXT,
    open REAL, high REAL, low REAL, close REAL, volume REAL, value REAL,
    PRIMARY KEY (date, code)
);
CREATE INDEX IF NOT EXISTS idx_prices_code ON prices(code, date);

CREATE TABLE IF NOT EXISTS index_prices (
    date TEXT NOT NULL, ticker TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (date, ticker)
);

CREATE TABLE IF NOT EXISTS scan_runs (
    base_date TEXT PRIMARY KEY,
    run_at TEXT, universe INTEGER, kept INTEGER,
    new_cnt INTEGER, hold_cnt INTEGER, drop_cnt INTEGER,
    watch_cnt INTEGER DEFAULT 0,
    regime TEXT, mode TEXT
);

CREATE TABLE IF NOT EXISTS positions (
    code TEXT PRIMARY KEY, name TEXT, market TEXT,
    entry_date TEXT, exit_date TEXT, exit_reason TEXT,
    entry_close REAL, last_close REAL, status TEXT
);

CREATE TABLE IF NOT EXISTS scan_results (
    base_date TEXT NOT NULL, code TEXT NOT NULL, name TEXT, market TEXT,
    state TEXT, score INTEGER, align_days INTEGER, disparity REAL,
    ma120_slope REAL, value_trend REAL, close REAL, change_pct REAL,
    vol_ratio REAL, reasons TEXT, score_detail TEXT,
    PRIMARY KEY (base_date, code)
);

CREATE TABLE IF NOT EXISTS watch_results (
    base_date TEXT NOT NULL, code TEXT NOT NULL, name TEXT, market TEXT,
    close REAL, change_pct REAL, vol_ratio REAL, disparity REAL,
    PRIMARY KEY (base_date, code)
);
"""


@contextmanager
def conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db():
    with conn() as c:
        c.executescript(SCHEMA)
        # 이미 GitHub에 배포된 DB(구버전 스키마)에 새 컬럼을 안전하게 추가한다.
        # 컬럼이 이미 있으면 오류가 나는데, 그건 무시하면 된다.
        try:
            c.execute("ALTER TABLE scan_runs ADD COLUMN watch_cnt INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE scan_results ADD COLUMN vol_ratio REAL")
        except sqlite3.OperationalError:
            pass


# --- 시세 -------------------------------------------------------------------
def saved_dates() -> set:
    with conn() as c:
        rows = c.execute("SELECT DISTINCT date FROM prices").fetchall()
    return {r["date"] for r in rows}


def save_day(date: str, df: pd.DataFrame, market: str):
    """df index=code, 컬럼: name/open/high/low/close/volume/value"""
    rows = [
        (date, str(code), market, r.get("name"), r.get("open"), r.get("high"),
         r.get("low"), r.get("close"), r.get("volume"), r.get("value"))
        for code, r in df.iterrows()
    ]
    with conn() as c:
        c.executemany(
            "INSERT OR REPLACE INTO prices "
            "(date,code,market,name,open,high,low,close,volume,value) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)", rows)


def save_index_day(date: str, ticker: str, row: dict):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO index_prices "
                  "(date,ticker,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?)",
                  (date, ticker, row.get("open"), row.get("high"), row.get("low"),
                   row.get("close"), row.get("volume")))


def load_panel(upto: str = None) -> pd.DataFrame:
    q = "SELECT date,code,market,name,open,high,low,close,volume,value FROM prices"
    params = []
    if upto:
        q += " WHERE date <= ?"
        params.append(upto)
    with conn() as c:
        df = pd.read_sql_query(q + " ORDER BY code, date", c, params=params)
    return df


def load_index(ticker: str) -> pd.DataFrame:
    with conn() as c:
        return pd.read_sql_query(
            "SELECT date,open,high,low,close,volume FROM index_prices "
            "WHERE ticker=? ORDER BY date", c, params=[ticker])


def load_stock(code: str) -> pd.DataFrame:
    with conn() as c:
        return pd.read_sql_query(
            "SELECT date,open,high,low,close,volume,value FROM prices "
            "WHERE code=? ORDER BY date", c, params=[code])


def latest_saved_date() -> str:
    with conn() as c:
        r = c.execute("SELECT MAX(date) d FROM prices").fetchone()
    return r["d"] if r and r["d"] else None


def trim_old_prices(keep_days: int = 200):
    """가격 테이블을 최근 keep_days 거래일만 남기고 정리한다.

    클라우드(예: GitHub Actions)에서는 매일 실행 결과를 저장소에 그대로
    커밋해서 다음 실행이 이어받는다. 5년치 전체를 계속 커밋하면 저장소가
    금방 커진다. 지표 계산에 필요한 건 최대 MA120(120거래일) + 여유분뿐이라,
    200거래일만 남겨도 충분하다.

    로컬 사용(5년 백테스트 등)에는 이 함수를 부르지 않으면 된다 — 호출은
    선택 사항이지, 자동으로 실행되지 않는다.
    """
    with conn() as c:
        dates = [r["date"] for r in
                 c.execute("SELECT DISTINCT date FROM prices ORDER BY date DESC "
                          f"LIMIT {int(keep_days)}").fetchall()]
        if not dates:
            return 0
        cutoff = min(dates)
        cur = c.execute("DELETE FROM prices WHERE date < ?", (cutoff,))
        c.execute("DELETE FROM index_prices WHERE date < ?", (cutoff,))
        return cur.rowcount


# --- 관찰 리스트 -------------------------------------------------------------
def active_positions() -> dict:
    with conn() as c:
        rows = c.execute("SELECT * FROM positions WHERE status='ACTIVE'").fetchall()
    return {r["code"]: dict(r) for r in rows}


def open_position(code, name, market, date, close):
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO positions "
            "(code,name,market,entry_date,exit_date,exit_reason,entry_close,last_close,status) "
            "VALUES (?,?,?,?,NULL,NULL,?,?, 'ACTIVE')",
            (code, name, market, date, close, close))


def touch_position(code, close):
    with conn() as c:
        c.execute("UPDATE positions SET last_close=? WHERE code=?", (close, code))


def close_position(code, date, reason, close=None):
    with conn() as c:
        c.execute("UPDATE positions SET status='EXITED', exit_date=?, exit_reason=?, "
                  "last_close=COALESCE(?, last_close) WHERE code=?",
                  (date, reason, close, code))


def recent_exits(limit: int = 50):
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM positions WHERE status='EXITED' "
            "ORDER BY exit_date DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# --- 스캔 결과 ---------------------------------------------------------------
def save_results(base_date: str, items: list):
    rows = [(base_date, i["code"], i.get("name"), i.get("market"), i["state"],
             i.get("score"), i.get("align_days"), i.get("disparity"),
             i.get("ma120_slope"), i.get("value_trend"), i.get("close"),
             i.get("change_pct"), i.get("vol_ratio"),
             json.dumps(i.get("reasons", []), ensure_ascii=False),
             json.dumps(i.get("score_detail", {}), ensure_ascii=False))
            for i in items]
    with conn() as c:
        # 같은 기준일을 다시 스캔하면 이전 결과를 먼저 지운다.
        # (지우지 않으면 편출이 취소돼도 옛 행이 남아 화면에 유령 종목이 뜬다)
        c.execute("DELETE FROM scan_results WHERE base_date=?", (base_date,))
        c.executemany(
            "INSERT OR REPLACE INTO scan_results "
            "(base_date,code,name,market,state,score,align_days,disparity,"
            "ma120_slope,value_trend,close,change_pct,vol_ratio,reasons,score_detail) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)


def save_run(base_date, run_at, universe, kept, n_new, n_hold, n_drop, regime, mode,
             n_watch=0):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO scan_runs "
                  "(base_date,run_at,universe,kept,new_cnt,hold_cnt,drop_cnt,watch_cnt,"
                  "regime,mode) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (base_date, run_at, universe, kept, n_new, n_hold, n_drop, n_watch,
                   json.dumps(regime, ensure_ascii=False), mode))


def load_results(base_date: str = None) -> list:
    with conn() as c:
        if base_date is None:
            r = c.execute("SELECT MAX(base_date) d FROM scan_results").fetchone()
            base_date = r["d"] if r else None
        if not base_date:
            return []
        rows = c.execute("SELECT * FROM scan_results WHERE base_date=? "
                         "ORDER BY score DESC", (base_date,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["reasons"] = json.loads(d.get("reasons") or "[]")
        d["score_detail"] = json.loads(d.get("score_detail") or "{}")
        out.append(d)
    return out


def last_run() -> dict:
    with conn() as c:
        r = c.execute("SELECT * FROM scan_runs ORDER BY base_date DESC LIMIT 1").fetchone()
    if not r:
        return {}
    d = dict(r)
    d["regime"] = json.loads(d.get("regime") or "{}")
    return d


def save_watch_results(base_date: str, items: list):
    """관찰 후보(참고용) 저장. 재실행 시 그 날짜 것은 지우고 다시 쓴다."""
    with conn() as c:
        c.execute("DELETE FROM watch_results WHERE base_date=?", (base_date,))
        c.executemany(
            "INSERT OR REPLACE INTO watch_results "
            "(base_date,code,name,market,close,change_pct,vol_ratio,disparity) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [(base_date, i["code"], i.get("name"), i.get("market"), i.get("close"),
              i.get("change_pct"), i.get("vol_ratio"), i.get("disparity")) for i in items])


def load_watch_results(base_date: str = None) -> list:
    with conn() as c:
        if base_date is None:
            r = c.execute("SELECT MAX(base_date) d FROM watch_results").fetchone()
            base_date = r["d"] if r else None
        if not base_date:
            return []
        rows = c.execute("SELECT * FROM watch_results WHERE base_date=? "
                         "ORDER BY vol_ratio DESC", (base_date,)).fetchall()
    return [dict(r) for r in rows]


def run_history(limit: int = 60) -> list:
    """1군 종목 수 추이 = 무료 시장 국면 지표."""
    with conn() as c:
        rows = c.execute("SELECT base_date, universe, kept FROM scan_runs "
                         "ORDER BY base_date DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in reversed(rows)]
