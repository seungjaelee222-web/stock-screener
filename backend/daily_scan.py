# -*- coding: utf-8 -*-
"""
매일 새벽 자동 실행 진입점

  python daily_scan.py            # 수집 + 스캔 + 알림
  python daily_scan.py --no-notify

시간표 권장
  05:00  이 스크립트 실행 (수집 + 스캔 + 저장)
  05:30  실패 시 1회 재시도
  07:30  알림 발송  (--notify-only 로 저장된 결과만 다시 발송)

07:30이 아니라 05:00에 돌리는 이유는 실패했을 때 재시도할 여유를 두기 위해서다.

기준일 주의
  '어제'가 아니라 '최근 영업일'이다. 월요일 아침에 실행하면 기준일은
  일요일이 아니라 직전 금요일이 된다. collector.latest_business_day()가
  실제 시세가 존재하는 마지막 날짜를 직접 찾아서 확인한다.

KRX 로그인 안내
  이 프로그램을 실행하면 KRX_ID/KRX_PW 환경변수가 없다는 안내가 뜰 수
  있다. 최근 KRX 서버는 로그인 없는 요청에 빈 응답만 주는 경우가 있어,
  수집이 전부 실패한다면 가장 먼저 이 안내를 확인해야 한다.
  https://data.krx.co.kr 에서 무료로 계정을 만들 수 있다.
"""
import argparse
import os
import smtplib
import sys
import traceback
from email.mime.text import MIMEText
from email.header import Header

import collector
import datastore
import pipeline

# --- 알림 설정: 환경변수로 넣는다 (코드에 비밀번호를 쓰지 말 것) ---
SMTP_HOST = os.environ.get("SCREENER_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SCREENER_SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SCREENER_SMTP_USER", "seungjaelee222@gmail.com")
SMTP_PASS = os.environ.get("SCREENER_SMTP_PASS", "")   # Gmail은 앱 비밀번호
MAIL_TO = os.environ.get("SCREENER_MAIL_TO", "seungjaelee222@gmail.com")  # 기본 수신 주소
TG_TOKEN = os.environ.get("SCREENER_TG_TOKEN", "")
TG_CHAT = os.environ.get("SCREENER_TG_CHAT", "")


def send_mail(subject: str, body: str) -> bool:
    if not (SMTP_USER and SMTP_PASS and MAIL_TO):
        print("[알림] 메일 설정이 없어 건너뜁니다.")
        return False
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = SMTP_USER
    msg["To"] = MAIL_TO
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as s:
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(SMTP_USER, [MAIL_TO], msg.as_string())
    print(f"[알림] 메일 발송 완료 -> {MAIL_TO}")
    return True


def send_telegram(text: str) -> bool:
    if not (TG_TOKEN and TG_CHAT):
        return False
    import urllib.parse
    import urllib.request
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": text}).encode()
    urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15)
    print("[알림] 텔레그램 발송 완료")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=5.0)
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("--notify-only", action="store_true",
                    help="스캔 없이 저장된 마지막 결과만 다시 발송")
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    datastore.init_db()

    if args.notify_only:
        run = datastore.last_run()
        items = datastore.load_results()
        if not run:
            print("[오류] 저장된 스캔 결과가 없습니다. 먼저 스캔을 실행하세요.")
            sys.exit(1)
        result = {"base_date": run.get("base_date"), "universe": run.get("universe", 0),
                  "new": run.get("new_cnt", 0), "hold": run.get("hold_cnt", 0),
                  "drop": run.get("drop_cnt", 0), "regime": run.get("regime", {}),
                  "items": items}
    else:
        try:
            if args.demo:
                collector.seed_demo()
                result = pipeline.run_scan(base_date=datastore.latest_saved_date(),
                                           mode="demo")
            else:
                collector.check_login()
                result = pipeline.daily_job(years=args.years)
        except Exception as e:
            traceback.print_exc()
            if not args.no_notify:
                try:
                    send_mail("[정배열 스크리너] 스캔 실패", f"오류: {e}")
                except Exception:
                    pass
            sys.exit(1)

    # 콘솔/로그에는 분석용 상세 리포트를 남긴다.
    print(pipeline.summarize(result))

    # 메일은 종목 리스트만 — 사유·점수 같은 분석 항목은 넣지 않는다.
    mail_body = pipeline.summarize_list_only(result)

    if not args.no_notify:
        subject = f"정배열 스크리너 {result['base_date']}"
        try:
            send_mail(subject, mail_body)
        except Exception as e:
            print(f"[알림] 메일 실패: {e}")
        try:
            send_telegram(mail_body)
        except Exception as e:
            print(f"[알림] 텔레그램 실패: {e}")


if __name__ == "__main__":
    main()
