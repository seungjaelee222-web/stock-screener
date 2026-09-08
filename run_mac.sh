#!/bin/bash
cd "$(dirname "$0")/backend" || exit 1

PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then
  echo "[오류] 파이썬을 찾지 못했습니다. https://www.python.org/downloads/ 에서 설치하세요."
  read -r -p "엔터를 누르면 닫힙니다."; exit 1
fi
echo "파이썬: $($PY --version)"

if [ ! -d venv ]; then
  echo "[최초 1회] 가상환경을 만듭니다."
  "$PY" -m venv venv || { echo "[오류] 가상환경 생성 실패"; read -r -p "엔터"; exit 1; }
fi
source venv/bin/activate

python -m pip install --upgrade pip -q
if ! python -m pip install -r requirements.txt; then
  echo ""
  echo "[오류] 패키지 설치에 실패했습니다. 위의 빨간 메시지를 그대로 알려주세요."
  read -r -p "엔터를 누르면 닫힙니다."; exit 1
fi

python -c "import flask, pandas, numpy" || {
  echo "[오류] 패키지를 불러오지 못했습니다."; read -r -p "엔터"; exit 1; }

PORT="${SCREENER_PORT:-5173}"
if lsof -i ":$PORT" >/dev/null 2>&1; then
  echo "[알림] $PORT 번 포트를 이미 다른 프로그램이 쓰고 있습니다. 5174로 바꿉니다."
  export SCREENER_PORT=5174; PORT=5174
fi

echo ""
echo "  브라우저에서 http://localhost:$PORT 을 여세요. (이 창은 켜 두셔야 합니다)"
echo ""
python app.py
echo ""
echo "[서버가 멈췄습니다] 위 메시지를 확인하세요."
read -r -p "엔터를 누르면 닫힙니다."
