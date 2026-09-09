# -*- coding: utf-8 -*-
"""
정배열 스크리너 — 웹 뷰어 (읽기 전용)

이 서버는 스캔을 실행하지 않는다. 스캔은 daily_scan.py가 cron으로 처리하고,
이 서버는 SQLite에 이미 저장된 결과를 읽어서 보여주기만 한다.

그래서 API가 두 개뿐이다.
  GET /api/list         현재 보유 중인 종목 (이름·코드·가격·등락률)
  GET /api/chart/<code> 캔들 + 이동평균선

차트를 보고 싶을 때만 잠깐 켜면 된다. 매일 새벽까지 켜 둘 필요가 없다.
"""
import os
import re

from flask import Flask, jsonify, request, send_from_directory

import datastore
import screener

FRONTEND_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "frontend"))
PORT = int(os.environ.get("SCREENER_PORT", "5173"))
CODE_RE = re.compile(r"^[0-9A-Z]{5,6}$")

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/list")
def list_stocks():
    items = datastore.load_results()
    kept = [i for i in items if i["state"] in ("NEW", "HOLD")]
    kept.sort(key=lambda x: x.get("score") or 0, reverse=True)
    slim = [{"code": i["code"], "name": i["name"], "market": i["market"],
             "close": i["close"], "change_pct": i["change_pct"],
             "state": i["state"]} for i in kept]
    run = datastore.last_run()
    regime = (run.get("regime") or {}).get("label", "-")
    return jsonify({"ok": True, "items": slim,
                    "base_date": run.get("base_date"), "regime": regime})


@app.route("/api/watch")
def watch_stocks():
    """관찰 후보 (참고용, 정배열 아님) — /api/list 와 반드시 분리해서 제공한다."""
    items = datastore.load_results()
    watch = [i for i in items if i["state"] == "WATCH"]
    watch.sort(key=lambda x: x.get("vol_ratio") or 0, reverse=True)
    slim = [{"code": i["code"], "name": i["name"], "market": i["market"],
             "close": i["close"], "change_pct": i["change_pct"],
             "vol_ratio": i.get("vol_ratio"), "disparity": i.get("disparity"),
             "reasons": i.get("reasons") or []}
            for i in watch]
    run = datastore.last_run()
    return jsonify({"ok": True, "items": slim, "base_date": run.get("base_date"),
                    "note": "정배열 확정 신호가 아닙니다. 참고용 후보입니다."})


@app.route("/api/chart/<code>")
def chart(code):
    if not CODE_RE.match(code or ""):
        return jsonify({"ok": False, "message": "종목코드 형식이 아닙니다."}), 400
    df = datastore.load_stock(code)
    if df.empty:
        return jsonify({"ok": False, "message": "저장된 시세가 없습니다."}), 404
    enriched = screener.compute_indicators(df)
    return jsonify({"ok": True, "data": screener.to_chart_json(enriched)})


if __name__ == "__main__":
    datastore.init_db()
    print("=" * 56)
    print(f"  정배열 스크리너 ver2.0 · 웹 뷰어(로컬)   http://localhost:{PORT}")
    print("  (스캔은 하지 않습니다 — daily_scan.py가 새벽에 처리)")
    print("=" * 56)
    app.run(host="127.0.0.1", port=PORT, debug=False)
