# PDF 가격 검수기 - 로컬 AI 모드

이 프로젝트는 **Windows PC에서만 실행하는 로컬 웹서비스**입니다.
브라우저 주소는 `http://127.0.0.1:8000`만 사용하며, 외부에 공개하지 않습니다.

## 구성
- **FastAPI** 로컬 서버
- **PyMuPDF** 기반 상품 카드 분리
- **OpenAI API** 기반 상품 정보 추출
- **엑셀 기준단가** 비교
- **할인 상품 처리**
  - 빨간색 가격/문구 감지 시 `할인적용`으로 표시
  - 할인율(예: 10%)이 있으면 기준가격에 할인율을 적용해 비교
  - 할인율이 없으면 오류로 보지 않고 안내성 상태로 표시
- **결과 강조 표시**
  - 오류 가격: 빨간색
  - 할인 적용 가격: 주황색

## 내가 권하는 사용 방식
1. **로컬에서만 실행**
2. OpenAI 키는 `.env` 또는 Windows 환경 변수에만 저장
3. PDF 전체가 아니라 **상품 카드 단위**로 잘라 AI에 보내서 속도와 비용 최적화
4. 비교 규칙은 코드로 처리하고, AI는 정보 추출에만 사용

## 1. 준비
### Python 설치
- Python 3.11 이상 권장
- 설치 후 터미널에서 `python --version` 확인

### VS Code 권장 확장
- Python
- Pylance

## 2. 폴더 열기
VS Code에서 이 프로젝트 폴더를 엽니다.

## 3. 가상환경 생성
PowerShell 또는 VS Code 터미널에서 실행:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

## 4. 패키지 설치
```powershell
pip install -r requirements.txt
```

## 5. OpenAI 키 설정
### 방법 A: `.env` 파일 사용
1. `.env.example`을 복사해서 `.env` 파일 생성
2. `OPENAI_API_KEY=` 뒤에 실제 키 입력

예:
```env
OPENAI_API_KEY=sk-여기에실제키
OPENAI_MODEL=gpt-4.1-mini
AI_ENABLED=true
HOST=127.0.0.1
PORT=8000
MAX_PARALLEL_CARDS=4
```

### 방법 B: Windows 환경 변수
```powershell
setx OPENAI_API_KEY "sk-여기에실제키"
```
터미널을 다시 열어야 반영됩니다.

## 6. 엑셀 기준파일 확인
기준 엑셀은 아래 경로를 사용합니다.

```text
data/master.xlsx
```

이미 프로젝트에 넣어두었습니다.

## 7. 실행
### 가장 쉬운 방법
`run_local.bat` 더블클릭

### VS Code 터미널에서 직접 실행
```powershell
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

브라우저에서 열기:

```text
http://127.0.0.1:8000
```

## 8. 동작 방식
1. PDF 업로드
2. 페이지에서 상품 카드 영역 자동 분리
3. 각 카드 이미지 + 카드 내부 텍스트를 AI에 전송
4. AI가 상품명, 규격, 가격, 할인율, 빨간 가격 여부를 JSON으로 반환
5. 코드가 엑셀 기준 데이터와 매칭 후 권역별 비교
6. 결과 표 출력

## 9. 권역 비교 규칙
- **수도권**: 규격단가, KG단가, 개당단가 비교
- **경상권**: 규격단가, 개당단가 비교
- **경남 부산**: 규격단가, 개당단가 비교
- **호남권**: 규격단가, 개당단가 비교

## 10. 성능/비용 최적화
- 캐시 파일: `cache/ai_parse_cache.json`
- 동일 카드 이미지는 재분석 시 API 재호출을 줄임
- 동시에 처리할 카드 수는 `.env`의 `MAX_PARALLEL_CARDS`로 조정
- 너무 높이면 속도는 빨라질 수 있지만 API 호출 실패가 늘 수 있으므로 `4~6` 권장

## 11. 로컬 보안 수칙
- 절대 `0.0.0.0`로 열지 마세요. `127.0.0.1` 유지 권장
- `.env`를 외부 공유 금지
- OpenAI 키를 자바스크립트/브라우저 코드에 넣지 마세요

## 12. 자주 막히는 부분
### PowerShell 실행 정책 오류
다음 명령을 한 번 실행 후 다시 시도:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### 엑셀 파일 경로 오류
`data/master.xlsx`가 있는지 확인

### OpenAI 키 오류
- `.env` 저장 후 서버 재시작
- 또는 `setx` 후 새 터미널 열기

### AI 없이 테스트하고 싶을 때
`.env`에서 아래처럼 설정:
```env
AI_ENABLED=false
```
이 경우 규칙 기반 추출만 사용합니다.

## 13. 파일 구조
```text
app/
  main.py
  ai_parser.py
  comparison.py
  config.py
  excel_matcher.py
  models.py
  pdf_cropper.py
  utils.py
  templates/
  static/
data/
  master.xlsx
cache/
temp/
.env.example
requirements.txt
run_local.bat
```
