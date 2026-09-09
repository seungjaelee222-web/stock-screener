# -*- coding: utf-8 -*-
"""
스캔 파이프라인

매일 하는 일
  1) 최근 영업일까지 시세를 증분 적재한다
  2) 관찰 리스트(positions)에 있는 기존 종목의 '유지 조건'을 먼저 본다
     -> 하나라도 깨지면 사유를 붙여 즉시 편출한다
  3) 나머지 전 종목에 대해 '편입 조건'을 본다
     -> 통과하면 편입한다 (하루에 0개여도 정상)
  4) 결과와 시장 국면을 저장한다
"""
from datetime import datetime

import pandas as pd

import collector
import datastore
import screener


def _panel_by_code(panel: pd.DataFrame) -> dict:
    out = {}
    for code, g in panel.groupby("code"):
        out[str(code)] = g.sort_values("date").reset_index(drop=True)
    return out


def run_scan(base_date: str = None, mode: str = "live", progress_cb=None,
             caps: dict = None) -> dict:
    datastore.init_db()

    panel = datastore.load_panel(upto=base_date)
    if panel.empty:
        raise RuntimeError("적재된 시세가 없습니다. 먼저 데이터를 수집하세요.")
    base_date = base_date or panel["date"].max()

    by_code = _panel_by_code(panel)
    incumbents = datastore.active_positions()
    caps = caps or {}

    items, n_new, n_hold, n_drop = [], 0, 0, 0
    watch_items = []
    total = len(by_code)

    for i, (code, df) in enumerate(by_code.items()):
        if progress_cb and i % 50 == 0:
            progress_cb(i, total)

        name = df["name"].iloc[-1] if "name" in df.columns else ""
        market = df["market"].iloc[-1] if "market" in df.columns else ""
        try:
            ind = screener.compute_indicators(df)
        except Exception:
            continue

        is_inc = code in incumbents
        res = screener.evaluate(ind, name=name, market_cap=caps.get(code),
                                is_incumbent=is_inc)
        res.update({"code": code, "name": name, "market": market})

        if res["state"] == "NEW":
            datastore.open_position(code, name, market, base_date, res.get("close"))
            n_new += 1
            items.append(res)
        elif res["state"] == "HOLD":
            datastore.touch_position(code, res.get("close"))
            n_hold += 1
            items.append(res)
        elif res["state"] == "DROP":
            reason = " / ".join(res.get("reasons", [])) or "조건 이탈"
            datastore.close_position(code, base_date, reason, res.get("close"))
            n_drop += 1
            items.append(res)
        # NONE 은 저장하지 않는다 (관찰 대상이 아니었고 지금도 아님)

        # --- 관찰 후보 (참고용, 정배열 아님) ---
        # 이미 정배열 리스트(NEW/HOLD)에 있는 종목은 중복 표시하지 않는다.
        if res["state"] not in ("NEW", "HOLD") and not screener.is_excluded_name(name) \
                and not screener.has_trading_halt(ind):
            w = screener.evaluate_watch(ind)
            if w.get("passes"):
                last = ind.iloc[-1]
                prev_close = ind["close"].iloc[-2] if len(ind) > 1 else last["close"]
                change_pct = round(float((last["close"] - prev_close) / prev_close * 100.0), 2) \
                    if prev_close else 0.0
                watch_item = {
                    "code": code, "name": name, "market": market, "state": "WATCH",
                    "close": float(last["close"]), "change_pct": change_pct,
                    "vol_ratio": w["vol_ratio"], "disparity": w["disparity"],
                    "sources": w["sources"], "reasons": w["reasons"],
                }
                watch_items.append(watch_item)

    if progress_cb:
        progress_cb(total, total)

    # 거래량 배수가 큰 순서로 상위 N개만 남긴다 (전부 보여주면 신호가 아니라 소음이 된다)
    # 두 신호 모두 확인된 종목을 우선하고, 그다음 거래량 배수 순.
    # (그냥 거래량 순으로만 정렬하면 스테이지6 단독 신호가 항상 뒤로 밀린다)
    watch_items.sort(key=lambda x: (-len(x.get("sources", [])), -(x["vol_ratio"] or 0)))
    watch_items = watch_items[: screener.CONFIG["watch_max_items"]]
    items.extend(watch_items)   # 같은 scan_results 테이블에 state=WATCH로 함께 저장

    idx = datastore.load_index(collector.INDEX_TICKER["KOSPI"])
    regime = screener.market_regime(idx) if not idx.empty else {"label": "판정 불가"}
    regime["group1_ratio"] = round((n_new + n_hold) / total * 100, 2) if total else 0

    datastore.save_results(base_date, items)
    datastore.save_run(base_date, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       total, n_new + n_hold, n_new, n_hold, n_drop, regime, mode,
                       n_watch=len(watch_items))

    return {"base_date": base_date, "universe": total, "new": n_new,
            "hold": n_hold, "drop": n_drop, "regime": regime, "items": items,
            "watch": watch_items}


def daily_job(years: float = 5.0, mode: str = "live") -> dict:
    """스케줄러가 호출하는 진입점. 수집 -> 분할점검 -> 스캔."""
    info = collector.backfill(years=years)
    base = pd.Timestamp(info["base_date"]).strftime("%Y-%m-%d")

    for code in collector.verify_splits():
        try:
            collector.refetch_stock(code)
        except Exception as e:
            print(f"[warn] 분할 보정 실패 {code}: {e}")

    try:
        caps = collector.market_caps(info["base_date"])
    except Exception:
        caps = {}

    return run_scan(base_date=base, mode=mode, caps=caps)


def summarize(result: dict) -> str:
    """상세 리포트 — 분석용. 콘솔/로그에 남길 때 쓴다."""
    lines = []
    r = result["regime"]
    lines.append(f"[정배열 스크리너] 기준일 {result['base_date']}")
    lines.append(f"시장 국면: {r.get('label')} (1군 비중 {r.get('group1_ratio')}%)")
    lines.append(f"편입 {result['new']} · 유지 {result['hold']} · 편출 {result['drop']}"
                 f" / 검사 {result['universe']}종목")
    lines.append("")

    news = [i for i in result["items"] if i["state"] == "NEW"]
    if news:
        lines.append("■ 신규 편입")
        for i in sorted(news, key=lambda x: -x["score"]):
            lines.append(f"  {i['name']}({i['code']}) {i['close']:,.0f}원 "
                         f"{i['change_pct']:+.1f}% | 점수 {i['score']} | "
                         f"정배열 {i['align_days']}일차 | 이격도 {i['disparity']}%")
    else:
        lines.append("■ 신규 편입 없음")

    drops = [i for i in result["items"] if i["state"] == "DROP"]
    if drops:
        lines.append("")
        lines.append("■ 편출 (조건 이탈)")
        for i in drops:
            lines.append(f"  {i['name']}({i['code']}) — {' / '.join(i['reasons'])}")

    holds = [i for i in result["items"] if i["state"] == "HOLD"]
    if holds:
        lines.append("")
        lines.append("■ 유지 중")
        for i in sorted(holds, key=lambda x: -x["score"])[:20]:
            lines.append(f"  {i['name']}({i['code']}) 점수 {i['score']} | "
                         f"정배열 {i['align_days']}일차 | 이격도 {i['disparity']}%")
    return "\n".join(lines)


def summarize_list_only(result: dict) -> str:
    """메일용 — 종목 리스트만. 사유·점수 같은 분석 항목은 넣지 않는다.

    보유 중(NEW+HOLD) 종목을 등락률 순으로 나열한다. 편출 종목은 이름만
    한 줄로 붙인다 — 왜 빠졌는지는 궁금하면 웹에서 차트로 확인하면 된다.
    """
    kept = [i for i in result["items"] if i["state"] in ("NEW", "HOLD")]
    kept.sort(key=lambda x: x.get("change_pct", 0), reverse=True)

    lines = [f"정배열 스크리너 {result['base_date']} 기준", ""]
    if not kept:
        lines.append("오늘 목록에 있는 종목이 없습니다.")
    else:
        for i in kept:
            mark = "신규" if i["state"] == "NEW" else "유지"
            lines.append(f"[{mark}] {i['name']}({i['code']})  "
                         f"{i['close']:,.0f}원  {i['change_pct']:+.1f}%")

    drops = [i["name"] for i in result["items"] if i["state"] == "DROP"]
    if drops:
        lines.append("")
        lines.append("빠진 종목: " + ", ".join(drops))

    watch = result.get("watch") or []
    if watch:
        lines.append("")
        lines.append("─" * 40)
        lines.append("[참고용 · 검증 안 됨] 관찰 후보")
        lines.append("정배열 확정 종목이 아닙니다. 아래는 단기 관심이 쏠렸거나")
        lines.append("이평선 구조가 막 바뀌었다는 신호일 뿐, 과거 데이터로")
        lines.append("검증되지 않았습니다.")
        for w in watch:
            tag = "+".join(w.get("sources") or []) or "관찰"
            detail = " / ".join(w.get("reasons") or [])
            lines.append(f"  [{tag}] {w['name']}({w['code']})  {w['close']:,.0f}원  "
                         f"{w['change_pct']:+.1f}%  {detail}")

    return "\n".join(lines)
