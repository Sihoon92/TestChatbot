# LLM Chat App

Switchable **Ollama / 사내 LLM** 채팅 애플리케이션 (FastAPI + LangGraph 백엔드, React + Vite 프론트엔드).

세션별 대화 메모리, SQLite 영속화, LLM 연결 테스트, 마크다운(표/볼드/mermaid) 렌더링 포함.

## Prerequisites

- Python 3.11+
- Node 18+
- (ollama 백엔드 사용 시) [Ollama](https://ollama.com/) 실행 + 모델 pull:
  ```
  ollama pull gemma3n:e4b
  ```

## 최초 설치 (한 번에)

```bash
python setup.py       # 또는 Windows에서 setup.bat 더블클릭
```

backend venv 생성 + `pip install -e ".[dev]"`, `backend/.env` 생성(.env.example 복사), frontend `npm install`을 한 번에 처리한다. 이미 설치된 항목은 건너뛴다.

## Backend setup

```bash
cd backend
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -e ".[dev]"
cp .env.example .env   # 필요에 맞게 편집
uvicorn app.main:app --reload
```

API: `http://localhost:8000`

## Frontend setup

```bash
cd frontend
npm install
npm run dev
```

Dev 서버: `http://localhost:5173` (`/api` 요청을 `:8000` 으로 프록시).

## 한 번에 실행 (핫리로드)

```bash
python dev.py            # .env 의 LLM_BACKEND
python dev.py internal   # 사내 LLM 백엔드로 강제
```

## LLM 백엔드 전환

`backend/.env` 의 `LLM_BACKEND` 로 전환한다:

- `LLM_BACKEND=ollama` — `OLLAMA_BASE_URL`, `OLLAMA_MODEL` 사용
- `LLM_BACKEND=internal` — `INTERNAL_LLM_BASE_URL`, `INTERNAL_LLM_API_KEY`, `INTERNAL_LLM_MODEL` 사용
  (사내 사설 CA 환경이면 `INTERNAL_LLM_CA_BUNDLE` 에 .pem 경로 지정, 프록시 우회가 필요하면 `BYPASS_PROXY=true`)

사이드바의 **LLM 연결 테스트** 버튼으로 현재 백엔드 연결/모델 목록을 확인할 수 있다(초록=성공, 빨강=실패).

## 코팅 초기조건 도출 (최소 설치 / 사내 PC)

채팅 앱과 무관한 순수 수치 파이프라인이라 fastapi·langgraph·Node 없이 돈다.
폐쇄망에서는 받아야 할 패키지 수가 곧 실패 확률이라 최소 세트를 따로 둔다.

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-coating.txt

# 설치 검증 (코팅 테스트만)
.\.venv\Scripts\python.exe -m pytest (Get-ChildItem tests\test_coating_*.py).FullName -q

# 실행 - 결과는 data\coating\reports\data_profile.md / .html
.\.venv\Scripts\python.exe -m app.coating.report --csv data\coating\raw\실데이터.csv
```

- 이 최소 설치에서는 `pip install -e .` 를 하지 않는다. 반드시 `backend/` 에서
  **`python -m ...`** 형태로 부른다(`python app/coating/report.py` 는 `app` 을 못 찾는다).
- 전체 앱(채팅 + 금형 + 코팅)까지 필요하면 대신 `pip install -e ".[all]"`.
- 입력 CSV 인코딩은 `utf-8-sig` → `cp949` 순으로 자동 판별한다. 후보는
  `.env` 의 `COATING_CSV_ENCODINGS` 로 바꾸고, 한 번만 강제할 땐 `--encoding` 을 쓴다.
- `pytest-asyncio` 가 없으므로 `Unknown config option: asyncio_mode` 경고가 뜬다(정상).

### 입력 소스 전환 (CSV / xlsx)

사내 실데이터가 문서보안(DRM)으로 암호화돼 있으면 python 이 파일 바이트를 직접
읽을 수 없다(`CSV 가 아니다 ... NASCA DRM`). DRM 은 등록된 애플리케이션 안에서만
복호화하므로, 같은 데이터를 xlsx 로 만들어 두고 **Excel(COM)을 통해** 읽는다.

`backend/.env` 두 줄로 전환한다.

```
COATING_INPUT_FORMAT=xlsx
COATING_INPUT_PATH=raw/실데이터.xlsx
COATING_XLSX_SHEET=            # 비우면 첫 시트
```

```powershell
.\.venv\Scripts\python.exe -m pip install xlwings   # xlsx 경로에서만 필요
.\.venv\Scripts\python.exe -m app.coating.report    # 설정대로 읽는다
# 한 번만 다르게 쓰고 싶으면
.\.venv\Scripts\python.exe -m app.coating.report --input raw\다른파일.xlsx --format xlsx --sheet 데이터
```

- 그 PC 에 **Excel 설치·라이선스가 필요**하다. 읽기는 느리고(COM 왕복), 시트당
  1,048,576 행이 한계다. 원본이 그보다 크면 xlsx 를 만드는 시점에 이미 잘리므로
  lot 단위로 파일을 나눈다(코드는 한계값에 정확히 걸리면 에러를 낸다).
- CSV 경로는 그대로 살아 있다. DRM 이 풀리면 `COATING_INPUT_FORMAT=csv` 로
  되돌리는 것만으로 복귀한다.
- `--csv` 는 `--input` 의 옛 이름으로 계속 동작한다.


## Tests

```bash
cd backend && pytest
cd frontend && npm test
```
