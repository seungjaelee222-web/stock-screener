# -*- coding: utf-8 -*-
"""
GitHub Pages용 정적 파일 내보내기

app.py(읽기 전용 서버)가 하던 일을, 서버 없이도 되게 만든 버전이다.
매일 스캔이 끝나면 이 모듈이 결과를 JSON 파일로 그대로 써낸다.

  docs/data/list.json          현재 보유 종목 (이름·코드·가격·등락률)
  docs/data/regime.json        시장 국면 + 최근 추이
  docs/data/charts/<code>.json 종목별 캔들+이동평균선

GitHub Pages는 이 docs/ 폴더를 그대로 정적 웹사이트로 서비스한다.
서버가 필요 없다 — 파일만 있으면 된다.
"""
import json
import os

import datastore
import screener


def _write(path: str, obj: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def export_static(result: dict, out_dir: str):
    """daily_job()/run_scan()의 반환값을 받아 out_dir 아래에 JSON을 쓴다."""
    items = [i for i in result["items"] if i["state"] in ("NEW", "HOLD")]
    items.sort(key=lambda x: x.get("score") or 0, reverse=True)

    slim = [{"code": i["code"], "name": i["name"], "market": i.get("market"),
             "close": i["close"], "change_pct": i["change_pct"],
             "state": i["state"], "score": i.get("score"),
             "align_days": i.get("align_days"), "disparity": i.get("disparity")}
            for i in items]

    _write(os.path.join(out_dir, "data", "list.json"), {
        "ok": True, "items": slim,
        "base_date": result.get("base_date"),
        "regime": (result.get("regime") or {}).get("label", "-"),
        "generated_at": result.get("base_date"),
    })

    run_history = datastore.run_history()
    _write(os.path.join(out_dir, "data", "regime.json"), {
        "regime": result.get("regime") or {},
        "history": run_history,
    })

    for i in items:
        df = datastore.load_stock(i["code"])
        if df.empty:
            continue
        enriched = screener.compute_indicators(df)
        chart = screener.to_chart_json(enriched)
        _write(os.path.join(out_dir, "data", "charts", f"{i['code']}.json"),
               {"ok": True, "data": chart})

    # 더 이상 보유하지 않는 종목의 지난 차트 파일은 지운다.
    # (안 지우면 예전 종목 차트가 계속 남아 저장소만 커진다)
    charts_dir = os.path.join(out_dir, "data", "charts")
    if os.path.isdir(charts_dir):
        keep = {f"{i['code']}.json" for i in items}
        for fname in os.listdir(charts_dir):
            if fname.endswith(".json") and fname not in keep:
                os.remove(os.path.join(charts_dir, fname))

    return {"exported": len(items)}
