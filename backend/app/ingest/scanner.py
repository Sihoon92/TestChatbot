"""업로드 폴더를 훑어 처리 대상 파일을 찾는다.

표준 라이브러리만 쓴다 — Excel 을 열지 않으므로 빠르고, 파일이 잠겨 있어도
해시는 읽힌다(잠금 판정은 실제로 열어보는 pipeline 의 몫이다).
"""
import hashlib
from pathlib import Path

from app.ingest.schemas import FoundFile

# xlwings 는 Excel 을 직접 띄우므로 구형 .xls 와 매크로 파일도 읽을 수 있다.
_EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls"}

# 사람이 파일을 열면 Excel 이 같은 폴더에 '~$이름.xlsx' 잠금 파일을 만든다.
# 실제 워크북이 아니라 열면 매번 실패한다.
_LOCK_PREFIX = "~$"

_HASH_CHUNK = 1 << 20  # 1MB


def file_hash(path: str) -> str:
    """파일 내용의 sha256.

    mtime 을 쓰지 않는 이유: 복사·이동만 해도 mtime 이 바뀌어 거짓 변경으로
    잡히고, 그때마다 배치 전체가 다시 돈다. 내용이 같으면 같은 해시여야 한다.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def scan(root: str, stage_dirs: dict[str, str]) -> list[FoundFile]:
    """root 아래 매핑된 폴더들에서 엑셀 파일을 찾는다.

    폴더가 없는 것은 정상이다 — 첫 실행에는 아무도 올리지 않았다.
    결과는 경로 오름차순으로 정렬한다: 같은 폴더 상태면 항상 같은 순서여야
    배치가 재현된다.
    """
    root_path = Path(root)
    found: list[FoundFile] = []

    for dir_name, kind in stage_dirs.items():
        directory = root_path / dir_name
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if not path.is_file():
                continue
            if path.name.startswith(_LOCK_PREFIX):
                continue
            if path.suffix.lower() not in _EXCEL_SUFFIXES:
                continue
            found.append(
                FoundFile(
                    path=str(path),
                    kind=kind,  # type: ignore[arg-type]
                    content_hash=file_hash(str(path)),
                )
            )

    found.sort(key=lambda f: f.path)
    return found
