@echo off
chcp 65001 >nul
cd /d "%~dp0backend"

where python >nul 2>&1
if errorlevel 1 (
  echo [오류] 파이썬을 찾지 못했습니다.
  echo        https://www.python.org/downloads/ 에서 설치하되,
  echo        설치 첫 화면의 "Add python.exe to PATH" 를 반드시 체크하세요.
  pause & exit /b 1
)
python --version

if not exist venv (
  echo [최초 1회] 가상환경을 만듭니다.
  python -m venv venv
  if errorlevel 1 ( echo [오류] 가상환경 생성 실패 & pause & exit /b 1 )
)
call venv\Scripts\activate.bat

python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo [오류] 패키지 설치에 실패했습니다. 위 메시지를 그대로 알려주세요.
  pause & exit /b 1
)

python -c "import flask, pandas, numpy"
if errorlevel 1 ( echo [오류] 패키지를 불러오지 못했습니다. & pause & exit /b 1 )

echo.
echo   브라우저에서 http://localhost:5173 을 여세요. (이 창은 켜 두셔야 합니다)
echo.
python app.py
echo.
echo [서버가 멈췄습니다] 위 메시지를 확인하세요.
pause
