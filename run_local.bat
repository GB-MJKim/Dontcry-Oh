@echo off
cd /d %~dp0
if not exist .venv (
  echo [1/3] Python 가상환경을 생성합니다...
  python -m venv .venv
)
call .venv\Scripts\activate.bat

echo [2/3] 필요한 패키지를 설치/확인합니다...
pip install -r requirements.txt

echo [3/3] 로컬 서버를 실행합니다...
echo 브라우저 주소: http://127.0.0.1:8000
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
