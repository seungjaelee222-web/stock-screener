# -*- coding: utf-8 -*-
"""
GitHub Actions(클라우드)에서 매일 실행되는 진입점

daily_scan.py와 역할은 같지만(수집→스캔→메일), 두 가지가 다르다.

  1) 5년이 아니라 1년만 수집한다. 클라우드에서는 매번 새로 켜지는 컴퓨터가
     아니라 저장소에 커밋된 데이터를 이어받는 구조라, 지표 계산에 필요한
     기간(최대 120거래일)보다 넉넉히만 있으면 된다. 5년치를 매일 저장소에
     다시 커밋하면 용량이 금방 커진다.
  2) 스캔 결과를 SQLite뿐 아니라 docs/ 폴더에 JSON으로도 내보낸다.
     GitHub Pages가 이 폴더를 그대로 웹사이트로 보여준다.

실행 순서: 로그인 확인 → 수집 → 오래된 데이터 정리 → 스캔 → 정적 파일
내보내기 → 메일 발송

이 파일은 사람이 직접 실행할 일이 거의 없다. .github/workflows/daily.yml이
매일 새벽 자동으로 호출한다. 로컬에서 전체 과정을 확인해보고 싶으면:

  python cloud_daily.py --no-notify
"""
import argparse
import os
import sys
import traceback

import collector
import daily_scan
import datastore
import export_static
import pipeline

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DOCS_DIR = os.path.join(REPO_ROOT, "docs")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=1.0,
                    help="클라우드는 롤링 보관이라 1년이면 충분하다")
    ap.add_argument("--keep-days", type=int, default=200)
    ap.add_argument("--no-notify", action="store_true")
    args = ap.parse_args()

    datastore.init_db()
    collector.check_login()

    try:
        result = pipeline.daily_job(years=args.years)
        removed = datastore.trim_old_prices(keep_days=args.keep_days)
        print(f"[정리] 오래된 시세 {removed}행 삭제 (최근 {args.keep_days}거래일만 유지)")
        export_static.export_static(result, DOCS_DIR)
        print(f"[내보내기] docs/ 폴더에 정적 파일 작성 완료")
    except Exception as e:
        traceback.print_exc()
        if not args.no_notify:
            try:
                daily_scan.send_mail("정배열 스크리너 — 스캔 실패", f"오류: {e}")
            except Exception:
                pass
        sys.exit(1)

    print(pipeline.summarize(result))
    mail_body = pipeline.summarize_list_only(result)

    if not args.no_notify:
        subject = f"정배열 스크리너 {result['base_date']}"
        try:
            daily_scan.send_mail(subject, mail_body)
        except Exception as e:
            print(f"[알림] 메일 실패: {e}")
        try:
            daily_scan.send_telegram(mail_body)
        except Exception as e:
            print(f"[알림] 텔레그램 실패: {e}")


if __name__ == "__main__":
    main()
