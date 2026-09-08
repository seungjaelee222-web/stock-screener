@echo off
REM 윈도우 자동 실행 등록. 이 파일을 관리자 권한으로 한 번만 실행하세요.
REM 메일 설정은 시스템 환경변수에 미리 넣어두어야 합니다.
REM   SCREENER_SMTP_USER / SCREENER_SMTP_PASS / SCREENER_MAIL_TO
REM 수신 주소는 seungjaelee222@gmail.com 이 기본값이라 SCREENER_MAIL_TO 는 생략해도 됩니다.
REM SCREENER_SMTP_PASS(앱 비밀번호)는 반드시 직접 넣으셔야 합니다.

set ROOT=%~dp0..
schtasks /Create /TN "정배열스크리너_일일스캔" /SC DAILY /ST 05:00 ^
  /TR "\"%ROOT%\backend\venv\Scripts\python.exe\" \"%ROOT%\backend\daily_scan.py\"" ^
  /RL LIMITED /F

schtasks /Create /TN "정배열스크리너_재시도" /SC DAILY /ST 05:30 ^
  /TR "\"%ROOT%\backend\venv\Scripts\python.exe\" \"%ROOT%\backend\daily_scan.py\"" ^
  /RL LIMITED /F

schtasks /Create /TN "정배열스크리너_알림" /SC DAILY /ST 07:30 ^
  /TR "\"%ROOT%\backend\venv\Scripts\python.exe\" \"%ROOT%\backend\daily_scan.py\" --notify-only" ^
  /RL LIMITED /F

echo 등록 완료. 작업 스케줄러에서 확인하세요.
pause
