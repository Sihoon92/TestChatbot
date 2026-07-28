"""업로드 폴더를 훑어 처리 대상 파일을 찾는다.

표준 라이브러리만 쓴다 — Excel 을 열지 않으므로 빠르다.
"""
import hashlib
import logging
from pathlib import Path
from typing import get_args

from app.ingest.schemas import FoundFile, ScanResult, SourceKind

logger = logging.getLogger(__name__)

# xlwings 는 Excel 을 직접 띄우므로 구형 .xls 와 매크로 파일도 읽을 수 있다.
_EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls"}

# 사람이 파일을 열면 Excel 이 같은 폴더에 '~$이름.xlsx' 잠금 파일을 만든다.
# 실제 워크북이 아니라 열면 매번 실패한다.
_LOCK_PREFIX = "~$"

_HASH_CHUNK = 1 << 20  # 1MB

# stage_dirs 의 kind 값이 유효한지 검증한다.
_VALID_KINDS = frozenset(get_args(SourceKind))


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


def scan(root: str, stage_dirs: dict[str, str]) -> ScanResult:
    """root 아래 매핑된 폴더들에서 엑셀 파일을 찾는다.

    stage_dirs 의 값이 유효한 소스 종류가 아니면 즉시 실패한다. 조용히 그
    폴더를 건너뛰면 운영자는 그 단계를 읽고 있다고 믿는데 실제로는 아무것도
    읽지 않는 상태가 된다 — 설정 오타는 데이터 오류와 달리 사람이 고쳐야 하고,
    고칠 수 있으려면 먼저 보여야 한다.

    읽을 수 없는 파일(사람이 엑셀을 열어둔 경우 등)은 files 대신 unreadable
    에 담아 돌려준다(경고 로그도 남긴다). 그 파일 하나 때문에 다른 폴더의
    정상 파일까지 유실되면 안 되지만, 조용히 사라져서도 안 된다 — 배치는
    DB 를 전체 교체하므로 빠진 파일의 데이터는 화면에서 사라진다. 건너뛴
    경로를 직접 돌려주는 이유는 호출자가 "삭제된 파일"과 "이번에 못 읽은
    파일"을 경로 존재 여부로 추론하면 틀리기 때문이다: 처음 등장하는 잠긴
    파일은 이력에 없어 아예 감지되지 않고, 설정에서 폴더 매핑을 뺀 경우가
    잠금으로 오인된다.

    폴더가 없는 것은 정상이다 — 첫 실행에는 아무도 올리지 않았다.
    결과는 경로 오름차순으로 정렬한다: 같은 폴더 상태면 항상 같은 순서여야
    배치가 재현된다.
    """
    # kind 검증: 설정 오타를 즉시 드러낸다.
    invalid = {n: k for n, k in stage_dirs.items() if k not in _VALID_KINDS}
    if invalid:
        raise ValueError(
            f"INGEST_STAGE_DIRS 에 유효하지 않은 소스 종류가 있다: {invalid}. "
            f"가능한 값: {sorted(_VALID_KINDS)}"
        )

    root_path = Path(root)
    found: list[FoundFile] = []
    unreadable: list[str] = []

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
            try:
                content_hash = file_hash(str(path))
            except OSError as exc:
                # 사람이 엑셀을 열어둔 상태에서 가장 흔하다. 이 파일만 건너뛰고
                # 다음 회차에 다시 시도한다 — 여기서 예외를 올리면 다른 폴더의
                # 정상 파일까지 통째로 유실된다. 다만 조용히 넘기지는 않는다:
                # 배치는 DB 를 전체 교체하므로, 빠진 파일의 데이터는 화면에서
                # 사라지는데 로그가 없으면 이유를 알 수 없다. 로그만으로는
                # 화면까지 닿지 않으므로 경로도 함께 돌려준다.
                logger.warning("파일을 읽지 못해 건너뛴다: %s (%s)", path, exc)
                unreadable.append(str(path))
                continue
            found.append(
                FoundFile(
                    path=str(path),
                    kind=kind,  # type: ignore[arg-type]
                    content_hash=content_hash,
                )
            )

    found.sort(key=lambda f: f.path)
    unreadable.sort()
    return ScanResult(files=found, unreadable=unreadable)
