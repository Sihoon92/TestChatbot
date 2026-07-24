"""최초 1회 설치 스크립트.

backend venv 생성 + pip install, backend/.env 생성(.env.example 복사),
frontend npm install 을 한 번에 처리한다. 이미 설치되어 있으면 건너뛴다.

사용법:
    python setup.py
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
IS_WIN = os.name == "nt"


def _run(cmd: list[str], cwd: Path) -> None:
    print(f"    $ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(cwd))
    if result.returncode != 0:
        print(f"[!] 실패: {' '.join(cmd)} (in {cwd})")
        sys.exit(result.returncode)


def setup_backend_venv() -> None:
    venv_dir = BACKEND / ".venv"
    venv_py = venv_dir / ("Scripts/python.exe" if IS_WIN else "bin/python")

    if venv_dir.exists():
        print("[skip] backend/.venv 가 이미 있어 건너뜀 (다시 설치하려면 폴더를 지우고 재실행)")
        return

    print("[1/3] backend/.venv 생성 중...")
    _run([sys.executable, "-m", "venv", str(venv_dir)], cwd=BACKEND)

    print("[1/3] backend 의존성 설치 중 (pip install -e \".[dev]\")...")
    _run([str(venv_py), "-m", "pip", "install", "-e", ".[dev]"], cwd=BACKEND)


def setup_backend_env_file() -> None:
    env_file = BACKEND / ".env"
    example = BACKEND / ".env.example"

    if env_file.exists():
        print("[skip] backend/.env 가 이미 있어 건너뜀")
        return

    print("[2/3] backend/.env 생성 중 (.env.example 복사)...")
    shutil.copyfile(example, env_file)
    print("      -> backend/.env 를 열어 필요한 값(LLM_BACKEND 등)을 채워줘")


def setup_frontend() -> None:
    node_modules = FRONTEND / "node_modules"

    if node_modules.exists():
        print("[skip] frontend/node_modules 가 이미 있어 건너뜀 (다시 설치하려면 폴더를 지우고 재실행)")
        return

    print("[3/3] frontend 의존성 설치 중 (npm install)...")
    npm = "npm.cmd" if IS_WIN else "npm"
    _run([npm, "install"], cwd=FRONTEND)


def main() -> None:
    print("=== 최초 설치 시작 ===\n")
    setup_backend_venv()
    print()
    setup_backend_env_file()
    print()
    setup_frontend()
    print("\n=== 설치 완료 ===")
    print("다음 명령으로 실행: python dev.py")


if __name__ == "__main__":
    main()
