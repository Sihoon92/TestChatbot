"""폴더 훑기. Excel 없이 tmp_path 로 전부 검증한다."""
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
