"""폴더 훑기. Excel 없이 tmp_path 로 전부 검증한다."""
import pytest

from app.ingest import scanner as scanner_module
from app.ingest.scanner import file_hash, scan

STAGE_DIRS = {"MES": "mes", "IQC": "iqc"}


def _make(root, rel, content=b"x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


def test_scan_finds_excel_files_and_labels_kind(tmp_path):
    _make(tmp_path, "MES/2026-07.xlsx")
    _make(tmp_path, "IQC/입고검사.xlsx")

    found = scan(str(tmp_path), STAGE_DIRS)

    kinds = sorted(f.kind for f in found)
    assert kinds == ["iqc", "mes"]


def test_scan_ignores_excel_lock_files(tmp_path):
    """사람이 파일을 열면 Excel 이 '~$이름.xlsx' 잠금 파일을 만든다.
    이걸 읽으려 하면 매번 실패한다."""
    _make(tmp_path, "MES/2026-07.xlsx")
    _make(tmp_path, "MES/~$2026-07.xlsx")

    found = scan(str(tmp_path), STAGE_DIRS)

    assert len(found) == 1
    assert "~$" not in found[0].path


def test_scan_ignores_non_excel_and_unmapped_dirs(tmp_path):
    _make(tmp_path, "MES/메모.txt")
    _make(tmp_path, "임시/2026-07.xlsx")

    assert scan(str(tmp_path), STAGE_DIRS) == []


def test_scan_accepts_xls_and_xlsm(tmp_path):
    """xlwings 는 Excel 을 직접 띄우므로 구형 .xls 와 매크로 파일도 읽는다."""
    _make(tmp_path, "MES/old.xls")
    _make(tmp_path, "MES/macro.xlsm")

    assert len(scan(str(tmp_path), STAGE_DIRS)) == 2


def test_scan_missing_root_returns_empty(tmp_path):
    """폴더가 아직 없는 것은 정상이다 — 첫 실행에는 아무도 안 올렸다."""
    assert scan(str(tmp_path / "없음"), STAGE_DIRS) == []


def test_scan_is_sorted_for_deterministic_batches(tmp_path):
    """같은 폴더 상태면 항상 같은 순서여야 재현이 된다."""
    _make(tmp_path, "MES/b.xlsx")
    _make(tmp_path, "MES/a.xlsx")

    paths = [f.path for f in scan(str(tmp_path), STAGE_DIRS)]
    assert paths == sorted(paths)


def test_hash_changes_with_content(tmp_path):
    p = _make(tmp_path, "MES/a.xlsx", b"one")
    first = file_hash(str(p))
    p.write_bytes(b"two")
    assert file_hash(str(p)) != first


def test_hash_ignores_mtime(tmp_path):
    """내용이 같으면 같은 해시다. mtime 을 쓰면 단순 복사·이동이 거짓 변경으로
    잡혀 매번 전체 재처리가 돈다."""
    import os
    import time

    p = _make(tmp_path, "MES/a.xlsx", b"same")
    first = file_hash(str(p))
    time.sleep(0.01)
    os.utime(p, None)
    assert file_hash(str(p)) == first


def test_scan_skips_unreadable_file_without_losing_others(tmp_path, monkeypatch):
    """사람이 엑셀을 열어둔 상황이 이 시스템에서 가장 흔하다. 그 파일 하나
    때문에 다른 폴더의 정상 파일까지 유실되면 안 된다."""
    _make(tmp_path, "MES/locked.xlsx")
    _make(tmp_path, "IQC/ok.xlsx")

    real = scanner_module.file_hash

    def _boom(path):
        if "locked" in path:
            raise PermissionError("사용 중")
        return real(path)

    monkeypatch.setattr(scanner_module, "file_hash", _boom)

    found = scan(str(tmp_path), STAGE_DIRS)

    assert [f.kind for f in found] == ["iqc"]


def test_scan_rejects_invalid_source_kind(tmp_path):
    """.env 오타로 유효하지 않은 종류가 오면 조용히 넘어가지 않는다.
    폴더가 비어 있어도 잡혀야 한다 — 설정 오류는 데이터와 무관하다."""
    with pytest.raises(ValueError, match="유효하지 않은 소스 종류"):
        scan(str(tmp_path), {"MES": "mess"})


def test_scan_accepts_uppercase_extension(tmp_path):
    """대문자 확장자도 엑셀이다. .suffix.lower() 가 실수로 빠져도 잡히게 한다."""
    _make(tmp_path, "MES/OLD.XLSX")
    assert len(scan(str(tmp_path), STAGE_DIRS)) == 1


def test_scan_ignores_files_without_extension(tmp_path):
    """확장자 없는 파일과 dot-file 은 엑셀이 아니다."""
    _make(tmp_path, "MES/README")
    _make(tmp_path, "MES/.gitkeep")
    assert scan(str(tmp_path), STAGE_DIRS) == []
