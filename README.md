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

## Tests

```bash
cd backend && pytest
cd frontend && npm test
```
