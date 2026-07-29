"""통합 개발 실행 스크립트 (핫리로드).

백엔드(uvicorn --reload, 기본 :8000)와 프론트엔드(vite HMR, :5173)를 동시에 띄우고,
둘 다 준비되면 브라우저를 자동으로 연다. Ctrl+C 하면 둘 다(자식 트리 포함) 종료한다.

사용법 (아무 파이썬으로나 실행 가능 — 백엔드는 venv 파이썬을 자동으로 찾아 씀):
    python dev.py                 # .env 의 LLM_BACKEND (기본 ollama)
    python dev.py internal        # 사내 LLM 백엔드로 강제
    python dev.py ollama          # ollama 백엔드로 강제
    python dev.py --llm internal

포트 바꾸기 (8000 점유 시): 환경변수 BACKEND_PORT 지정 — 백엔드와 프론트 프록시가
같이 따라간다. 예) PowerShell:  $env:BACKEND_PORT="8001"; python dev.py internal

접속 주소: 프론트(vite)는 0.0.0.0 에 바인딩되므로 localhost 와 이 PC 의 LAN IP
둘 다 항상 동시에 열려 있다 — 아무 브라우저에서나 원하는 쪽 주소를 그냥 열면 된다.
자동으로 열리는 탭만 --open 으로 고를 수 있다(둘 다 여전히 접속 가능한 건 동일):
    python dev.py --open local    # 기본값. http://localhost:5173 을 연다
    python dev.py --open lan      # 이 PC의 LAN IP(http://<ip>:5173)를 연다
"""

import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
IS_WIN = os.name == "nt"

def _resolve_backend_port() -> str:
    """백엔드 포트를 결정한다. 우선순위: 셸 환경변수 > backend/.env > 기본 8000.

    dev.py 는 pydantic 을 안 쓰므로 backend/.env 를 직접 훑어 BACKEND_PORT 만 읽는다.
    결정한 값은 os.environ 에 심어, 자식으로 뜨는 vite(프록시)도 같은 값을 보게 한다.
    """
    if os.environ.get("BACKEND_PORT"):
        return os.environ["BACKEND_PORT"]
    env_file = BACKEND / ".env"
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key.strip() == "BACKEND_PORT" and val.strip():
                return val.strip()
    return "8000"


# 백엔드 포트. 기본 8000, backend/.env 의 BACKEND_PORT 또는 셸 환경변수로 바꿀 수 있다.
# frontend/vite.config.ts 의 프록시도 같은 값을 읽으므로 백엔드/프론트가 자동으로 맞는다.
BACKEND_PORT = _resolve_backend_port()
os.environ["BACKEND_PORT"] = BACKEND_PORT  # 자식(vite) 프로세스도 같은 포트를 보게
BACKEND_URL = f"http://127.0.0.1:{BACKEND_PORT}/"
FRONTEND_URL = "http://localhost:5173/"


def _lan_ip() -> str | None:
    """이 PC 의 LAN IP. UDP 소켓은 실제로 연결/전송하지 않으므로 8.8.8.8 이
    안 떠 있어도(오프라인이어도) 로컬 라우팅 테이블에서 나가는 인터페이스의
    주소만 알아낸다 — 흔한 IP 자가진단 트릭이다."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


_lan_ip_addr = _lan_ip()
FRONTEND_URL_LAN = f"http://{_lan_ip_addr}:5173/" if _lan_ip_addr else None

# Windows: 자식을 별도 프로세스 그룹으로 띄워 Ctrl+C가 자식에 직접 전달되지 않게 한다.
_CREATE_GROUP = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WIN else 0

procs: list[subprocess.Popen] = []


def _backend_python() -> str:
    """백엔드 venv의 파이썬 경로 (없으면 현재 파이썬으로 폴백)."""
    venv_py = BACKEND / (".venv/Scripts/python.exe" if IS_WIN else ".venv/bin/python")
    if venv_py.exists():
        return str(venv_py)
    print("[!] backend/.venv 를 못 찾음. 먼저 venv 생성 + 의존성 설치가 필요해:")
    print('    cd backend && python -m venv .venv && pip install -e ".[dev]"')
    return sys.executable


def _parse_llm_backend(argv: list[str]) -> str | None:
    valid = {"ollama", "internal"}
    for i, a in enumerate(argv):
        if a == "--llm" and i + 1 < len(argv):
            return argv[i + 1]
        if a in valid:
            return a
    return None


def _parse_open_target(argv: list[str]) -> str:
    """자동으로 열 탭. 두 주소 다 항상 접속 가능하고, 이건 그중 뭘 자동으로
    여는지만 고른다 — 기본은 local(가장 안전: LAN IP 를 못 구해도 항상 된다)."""
    for i, a in enumerate(argv):
        if a == "--open" and i + 1 < len(argv):
            target = argv[i + 1]
            if target not in {"local", "lan"}:
                print(f"[!] --open 값은 local|lan 만 가능 (받은 값: {target!r}) - local 로 진행")
                return "local"
            return target
    return "local"


def start_backend(llm_backend: str | None = None) -> subprocess.Popen:
    env = os.environ.copy()
    if llm_backend:
        env["LLM_BACKEND"] = llm_backend
    # --reload-dir 로 app/ 만 감시 (app.db 변경에 의한 재시작 폭주 방지)
    return subprocess.Popen(
        [_backend_python(), "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(BACKEND_PORT),
         "--reload", "--reload-dir", str(BACKEND / "app")],
        cwd=str(BACKEND),
        creationflags=_CREATE_GROUP,
        env=env,
    )


def start_frontend() -> subprocess.Popen:
    npm = "npm.cmd" if IS_WIN else "npm"
    # env 를 명시적으로 넘겨 vite(node) 가 BACKEND_PORT 를 확실히 읽게 한다.
    return subprocess.Popen(
        [npm, "run", "dev"],
        cwd=str(FRONTEND),
        creationflags=_CREATE_GROUP,
        env=os.environ.copy(),
    )


def wait_for(url: str, label: str, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            print(f"[ok] {label} 준비됨 -> {url}")
            return True
        except Exception:
            if any(p.poll() is not None for p in procs):
                return False
            time.sleep(1)
    print(f"[!] {label} 가 {int(timeout)}초 안에 안 떴어 -> {url}")
    return False


def stop_all() -> None:
    print("\n[..] 종료 중...")
    for p in procs:
        try:
            if IS_WIN:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
            else:
                p.terminate()
        except Exception:
            pass


def main() -> None:
    llm_backend = _parse_llm_backend(sys.argv[1:])
    open_target = _parse_open_target(sys.argv[1:])
    backend_label = llm_backend or "(.env: LLM_BACKEND, 기본 ollama)"

    print("개발 서버 시작 중... (핫리로드 ON)")
    print(f"  backend  -> http://localhost:{BACKEND_PORT}  (app/ 변경 시 자동 재시작)")
    print("  frontend -> http://localhost:5173  (HMR, LAN IP 로도 접속 가능)")
    print(f"  LLM 백엔드 -> {backend_label}")
    print("  (전제: ollama -> Ollama 실행 중 / internal -> backend/.env 의 INTERNAL_LLM_* 설정)\n")

    procs.append(start_backend(llm_backend))
    procs.append(start_frontend())

    backend_ok = wait_for(BACKEND_URL, "backend")
    frontend_ok = wait_for(FRONTEND_URL, "frontend")

    if frontend_ok:
        if not backend_ok:
            print("[!] 백엔드가 아직 안 떴어 - UI는 열리지만 /api 호출은 실패할 수 있어.")

        # 둘 다 항상 동시에 열려 있다(vite host:true) — 아래는 그중 어느 쪽을
        # 이 PC 에서 자동으로 열지 고르는 것뿐이다. 다른 기기에서 접속할 땐
        # LAN 주소를 그대로 복사해서 쓰면 된다.
        print(f"  이 PC 에서:  {FRONTEND_URL}")
        if FRONTEND_URL_LAN:
            print(f"  다른 기기에서:  {FRONTEND_URL_LAN}")
        else:
            print("  [!] LAN IP 를 못 구했어 - 네트워크 어댑터가 없거나 오프라인인 것 같다.")

        target_url = FRONTEND_URL
        if open_target == "lan":
            if FRONTEND_URL_LAN:
                target_url = FRONTEND_URL_LAN
            else:
                print("  [!] --open lan 이 요청됐지만 LAN IP 를 못 구해 localhost 를 연다.")

        print(f"[->] 브라우저 여는 중... ({target_url})")
        webbrowser.open(target_url)
    else:
        print("[!] 프론트엔드가 안 떠서 브라우저를 안 열었어. 로그를 확인해줘.")

    print("\n둘 다 실행 중 (핫리로드). 종료하려면 Ctrl+C.\n")
    try:
        while True:
            time.sleep(1)
            for p in procs:
                if p.poll() is not None:
                    print("[!] 프로세스 중 하나가 종료됨 - 전체 종료할게.")
                    return
    except KeyboardInterrupt:
        pass
    finally:
        stop_all()


if __name__ == "__main__":
    main()
