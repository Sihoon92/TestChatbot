"""배치 실행. Excel 없이 가짜 워크북과 가짜 모델로 조립을 검증한다."""
from contextlib import contextmanager

import pytest

from app.config import Settings
from app.ingest import db, registry
from app.ingest.pipeline import run_ingest
from app.ingest.schemas import SheetLayout

MES_GRID = [
    ["금형번호", "상태", "호기"],
    ["RX28312", "사용중", "2"],
]
IQC_GRID = [
    ["금형번호", "punch"],
    ["RX28312", 12.5],
]


class FakeWorkbook:
    def __init__(self, rows):
        self._rows = rows

    def sheet_names(self):
        return ["Sheet1"]

    def used_values(self, sheet):
        return self._rows, "A1"

    def range_values(self, sheet, address):
        return self._rows

    def column_values(self, sheet, column, max_rows=5000):
        return [r[0] for r in self._rows]


def _layout(fields):
    return SheetLayout(
        sheet_name="Sheet1",
        anchors=[{"cell": "A1", "text": "금형번호"}],
        tables=[{
            "name": "상세", "role": "detail", "header_rows": [1],
            "data_start_row": 2,
            "columns": [{"field": f, "column": c} for f, c in fields],
        }],
    )


MES_LAYOUT = _layout([("mold_no", "A"), ("status", "B"), ("machine", "C")])
IQC_LAYOUT = _layout([("mold_no", "A"), ("punch", "B")])


@pytest.fixture
def env(tmp_path):
    (tmp_path / "MES").mkdir()
    (tmp_path / "IQC").mkdir()
    (tmp_path / "MES" / "mes.xlsx").write_bytes(b"mes")
    (tmp_path / "IQC" / "iqc.xlsx").write_bytes(b"iqc")
    settings = Settings(
        ingest_root=str(tmp_path),
        ingest_stage_dirs="MES:mes,IQC:iqc",
        molds_db_path=str(tmp_path / "molds.db"),
    )
    return settings


def _fake_open(grids):
    @contextmanager
    def _open(path):
        key = "mes" if "MES" in path else "iqc"
        yield FakeWorkbook(grids[key])
    return _open


def _fake_discover(layouts):
    def _discover(model, wb, kind, sheet_name, *, config=None):
        return layouts[kind]
    return _discover


def test_happy_path_writes_molds(env, monkeypatch):
    monkeypatch.setattr(
        "app.ingest.pipeline.discover_layout",
        _fake_discover({"mes": MES_LAYOUT, "iqc": IQC_LAYOUT}),
    )

    summary = run_ingest(env, model=object(),
                         open_wb=_fake_open({"mes": MES_GRID, "iqc": IQC_GRID}))

    assert summary.status == "ok"
    assert summary.mold_count == 1
    assert summary.iqc_matched == 1

    conn = db.connect(env.resolved_molds_db_path)
    row = conn.execute("SELECT * FROM mold").fetchone()
    assert row["mold_no"] == "RX28312"
    assert row["machine"] == "2"
    conn.close()


def test_second_run_is_skipped_when_nothing_changed(env, monkeypatch):
    monkeypatch.setattr(
        "app.ingest.pipeline.discover_layout",
        _fake_discover({"mes": MES_LAYOUT, "iqc": IQC_LAYOUT}),
    )
    opener = _fake_open({"mes": MES_GRID, "iqc": IQC_GRID})

    run_ingest(env, model=object(), open_wb=opener)
    second = run_ingest(env, model=object(), open_wb=opener)

    assert second.status == "skipped"


def test_missing_mes_aborts_without_touching_db(env, monkeypatch):
    """마스터가 없으면 금형 목록을 만들 수 없다. 부분 갱신은 옛 금형과 새
    부속정보가 섞인 상태를 만든다."""
    import os
    os.remove(os.path.join(env.resolved_ingest_root, "MES", "mes.xlsx"))
    monkeypatch.setattr(
        "app.ingest.pipeline.discover_layout",
        _fake_discover({"mes": MES_LAYOUT, "iqc": IQC_LAYOUT}),
    )

    summary = run_ingest(env, model=object(), open_wb=_fake_open({"iqc": IQC_GRID}))

    assert summary.status == "error"
    assert "MES" in (summary.error or "")


def test_cached_layout_avoids_calling_agent(env, monkeypatch):
    """앵커가 맞으면 에이전트를 부르지 않는다 — 이게 안 되면 파일마다
    LLM 이 돌아 느려지고 결과가 흔들린다."""
    calls = {"n": 0}

    def _counting(model, wb, kind, sheet_name, *, config=None):
        calls["n"] += 1
        return {"mes": MES_LAYOUT, "iqc": IQC_LAYOUT}[kind]

    monkeypatch.setattr("app.ingest.pipeline.discover_layout", _counting)
    opener = _fake_open({"mes": MES_GRID, "iqc": IQC_GRID})

    run_ingest(env, model=object(), open_wb=opener)
    assert calls["n"] == 2

    # 파일 내용을 바꿔 배치를 다시 돌게 하되, 헤더(앵커)는 그대로 둔다.
    import os
    with open(os.path.join(env.resolved_ingest_root, "MES", "mes.xlsx"), "wb") as f:
        f.write(b"mes-v2")

    second = run_ingest(env, model=object(), open_wb=opener)
    assert second.status == "ok", "두 번째 배치가 실제로 돌아야 캐시 경로를 증명한다"
    assert calls["n"] == 2, "앵커가 같으면 에이전트를 다시 부르지 않아야 한다"


def _bad_layout():
    """앵커는 맞는데 detail 표에 컬럼 매핑이 없어 parse_rows 가 터지는 레이아웃."""
    return SheetLayout(
        sheet_name="Sheet1",
        anchors=[{"cell": "A1", "text": "금형번호"}],
        tables=[{
            "name": "상세", "role": "detail", "header_rows": [1],
            "data_start_row": 2, "columns": [],
        }],
    )


def test_layout_is_not_cached_when_parsing_fails(env, monkeypatch):
    """파싱에 실패한 레이아웃은 캐시에 남지 않아야 한다. 저장이 파싱보다
    앞에 있으면(save_layout 은 즉시 커밋한다) 나쁜 레이아웃이 박혀서
    다음 회차에도 같은 앵커로 다시 뽑혀 영구 실패한다."""
    monkeypatch.setattr(
        "app.ingest.pipeline.discover_layout",
        _fake_discover({"mes": _bad_layout(), "iqc": IQC_LAYOUT}),
    )

    summary = run_ingest(env, model=object(),
                         open_wb=_fake_open({"mes": MES_GRID, "iqc": IQC_GRID}))

    assert summary.status == "error"
    conn = db.connect(env.resolved_molds_db_path)
    n = conn.execute(
        "SELECT COUNT(*) c FROM sheet_mapping WHERE kind = 'mes'"
    ).fetchone()["c"]
    conn.close()
    assert n == 0, "파싱에 실패한 레이아웃이 캐시에 저장되면 안 된다"


def test_bad_cached_layout_is_reinterpreted_once(env, monkeypatch):
    """이미 캐시에 박힌 나쁜 레이아웃에서 자력으로 빠져나온다.

    앵커는 맞는데 파싱이 안 되는 상황(양식이 앵커 밖에서 바뀜)이다. 재해석이
    없으면 그 파일은 영구히 실패하고 UI 로는 복구할 방법이 없다 —
    molds.db 를 손으로 지우는 것 외에는."""
    db.init_db(env.resolved_molds_db_path)
    conn = db.connect(env.resolved_molds_db_path)
    registry.save_layout(conn, "mes", _bad_layout(), "seed")
    registry.save_layout(conn, "iqc", _bad_layout(), "seed")
    conn.close()

    monkeypatch.setattr(
        "app.ingest.pipeline.discover_layout",
        _fake_discover({"mes": MES_LAYOUT, "iqc": IQC_LAYOUT}),
    )

    summary = run_ingest(env, model=object(),
                         open_wb=_fake_open({"mes": MES_GRID, "iqc": IQC_GRID}))

    assert summary.status == "ok"
    assert summary.mold_count == 1

    # 재해석 결과가 캐시에 남아, 다음 회차에는 앵커 대조만으로 통과해야 한다.
    conn = db.connect(env.resolved_molds_db_path)
    latest = registry.load_layouts(conn, "mes", "Sheet1")[0]
    conn.close()
    assert latest.tables[0].columns, "성공한 레이아웃이 최신으로 저장돼야 한다"


def test_broken_iqc_file_does_not_kill_the_batch(env, monkeypatch):
    """IQC 한 장이 깨져도 금형 목록은 갱신돼야 한다.

    부속 정보 하나가 없다고 금형 목록을 버릴 이유가 없다. MES 는 멀쩡히
    읽혔는데 replace_all 이 아예 안 돌면 화면이 통째로 옛 상태로 남는다."""
    def _boom_on_iqc(model, wb, kind, sheet_name, *, config=None):
        if kind == "iqc":
            raise RuntimeError("에이전트가 레이아웃을 제출하지 않았다")
        return MES_LAYOUT

    monkeypatch.setattr("app.ingest.pipeline.discover_layout", _boom_on_iqc)

    summary = run_ingest(env, model=object(),
                         open_wb=_fake_open({"mes": MES_GRID, "iqc": IQC_GRID}))

    assert summary.status == "ok"
    assert summary.mold_count == 1, "MES 는 읽혔으므로 금형 목록은 갱신된다"
    assert summary.iqc_matched == 0
    # 조용히 빠지면 안 된다 — 어느 파일이 왜 빠졌는지 화면까지 가야 한다.
    assert len(summary.failed_files) == 1
    assert "IQC" in summary.failed_files[0]
    assert "RuntimeError" in summary.failed_files[0]

    conn = db.connect(env.resolved_molds_db_path)
    assert conn.execute("SELECT COUNT(*) c FROM mold").fetchone()["c"] == 1
    # 이력에도 남아야 한다 — 새로고침하면 status 조회로 다시 읽힌다.
    assert registry.latest_run(conn).failed_files == summary.failed_files
    conn.close()


def test_broken_mes_file_still_aborts_the_batch(env, monkeypatch):
    """MES 는 격리하지 않는다. 마스터가 없으면 금형 목록 자체를 만들 수 없어
    부분 갱신이 옛 금형과 새 부속정보가 섞인 상태를 만든다."""
    def _boom_on_mes(model, wb, kind, sheet_name, *, config=None):
        if kind == "mes":
            raise RuntimeError("레이아웃 미제출")
        return IQC_LAYOUT

    monkeypatch.setattr("app.ingest.pipeline.discover_layout", _boom_on_mes)

    summary = run_ingest(env, model=object(),
                         open_wb=_fake_open({"mes": MES_GRID, "iqc": IQC_GRID}))

    assert summary.status == "error"
    conn = db.connect(env.resolved_molds_db_path)
    assert conn.execute("SELECT COUNT(*) c FROM mold").fetchone()["c"] == 0
    conn.close()


def test_run_summary_is_persisted(env, monkeypatch):
    monkeypatch.setattr(
        "app.ingest.pipeline.discover_layout",
        _fake_discover({"mes": MES_LAYOUT, "iqc": IQC_LAYOUT}),
    )
    run_ingest(env, model=object(), open_wb=_fake_open({"mes": MES_GRID, "iqc": IQC_GRID}))

    conn = db.connect(env.resolved_molds_db_path)
    latest = registry.latest_run(conn)
    conn.close()

    assert latest is not None
    assert latest.mold_count == 1


def _lock(monkeypatch, needle: str):
    """스캐너가 `needle` 이 든 경로를 못 읽은 것처럼 만든다(엑셀 열어둔 상황)."""
    from app.ingest import scanner as scanner_module

    real = scanner_module.file_hash

    def _boom(path):
        if needle in path:
            raise PermissionError("사용 중")
        return real(path)

    monkeypatch.setattr(scanner_module, "file_hash", _boom)


def test_unreadable_file_skips_batch_and_is_reported(env, monkeypatch):
    """사람이 엑셀을 열어둬 못 읽은 파일이 있으면 배치를 건너뛴다.
    배치는 DB 를 전체 교체하므로, 그냥 진행하면 그 파일의 데이터가 화면에서
    조용히 사라진다. 스캐너가 건너뛴 경로를 직접 돌려주므로 진짜 삭제와
    섞이지 않는다."""
    monkeypatch.setattr(
        "app.ingest.pipeline.discover_layout",
        _fake_discover({"mes": MES_LAYOUT, "iqc": IQC_LAYOUT}),
    )
    opener = _fake_open({"mes": MES_GRID, "iqc": IQC_GRID})

    first = run_ingest(env, model=object(), open_wb=opener)
    assert first.status == "ok"

    _lock(monkeypatch, "IQC")
    second = run_ingest(env, model=object(), open_wb=opener)

    assert second.status == "skipped"
    assert any("IQC" in p for p in second.unreadable_files)

    # DB 가 그대로여야 한다 — 첫 배치의 IQC 항목이 살아 있어야 한다.
    conn = db.connect(env.resolved_molds_db_path)
    n = conn.execute("SELECT COUNT(*) c FROM mold_stage").fetchone()["c"]
    conn.close()
    assert n == 1, "배치를 건너뛰었으므로 이전 IQC 데이터가 남아 있어야 한다"


def test_first_seen_locked_file_is_reported(env, monkeypatch):
    """처음 등장하는 파일이 잠겨 있어도 감지돼야 한다.

    이력(known)에 없다는 이유로 못 보면 status='ok', iqc_matched=0 인데
    화면에는 아무 흔적이 없다 — 사용자는 IQC 를 올렸다고 믿는다."""
    monkeypatch.setattr(
        "app.ingest.pipeline.discover_layout",
        _fake_discover({"mes": MES_LAYOUT, "iqc": IQC_LAYOUT}),
    )
    _lock(monkeypatch, "IQC")

    summary = run_ingest(env, model=object(),
                         open_wb=_fake_open({"mes": MES_GRID, "iqc": IQC_GRID}))

    assert summary.status == "skipped"
    assert any("IQC" in p for p in summary.unreadable_files)


def test_unmapped_dir_history_is_cleaned_not_treated_as_locked(env, monkeypatch):
    """.env 에서 폴더 매핑을 빼면 그 파일 이력은 정리돼야 한다.

    경로 존재 여부로 판정하던 시절에는 파일이 디스크에 남아 있어 매 회차
    unreadable 로 잡혀 영구히 skipped 였다. 이력 정리는 성공 경로에만 있어
    자력 탈출이 불가능했고, 화면 문구("파일을 닫고 다시 실행하세요")도 틀렸다."""
    monkeypatch.setattr(
        "app.ingest.pipeline.discover_layout",
        _fake_discover({"mes": MES_LAYOUT, "iqc": IQC_LAYOUT}),
    )
    opener = _fake_open({"mes": MES_GRID, "iqc": IQC_GRID})

    assert run_ingest(env, model=object(), open_wb=opener).status == "ok"

    # IQC 폴더를 매핑에서 뺀다. 파일은 디스크에 그대로 남아 있다.
    narrowed = env.model_copy(update={"ingest_stage_dirs": "MES:mes"})
    second = run_ingest(narrowed, model=object(), open_wb=opener)

    assert second.status == "ok", "잠금으로 오인해 건너뛰면 안 된다"
    assert second.unreadable_files == []

    conn = db.connect(env.resolved_molds_db_path)
    paths = [r["path"] for r in conn.execute("SELECT path FROM ingested_file")]
    conn.close()
    assert not any("IQC" in p for p in paths), "매핑에서 빠진 파일 이력은 정리된다"


def test_unprocessed_kind_is_not_recorded_as_read(env, monkeypatch):
    """1단계가 읽지 않는 폴더(PQC 등)의 파일을 '읽었다'고 기록하면 안 된다.

    이력에 남으면 화면은 성공인데 데이터는 안 들어오고 경고도 없다."""
    import os
    pqc_dir = os.path.join(env.resolved_ingest_root, "PQC")
    os.makedirs(pqc_dir, exist_ok=True)
    with open(os.path.join(pqc_dir, "pqc.xlsx"), "wb") as f:
        f.write(b"pqc")

    with_pqc = env.model_copy(
        update={"ingest_stage_dirs": "MES:mes,IQC:iqc,PQC:pqc"}
    )
    monkeypatch.setattr(
        "app.ingest.pipeline.discover_layout",
        _fake_discover({"mes": MES_LAYOUT, "iqc": IQC_LAYOUT}),
    )

    summary = run_ingest(with_pqc, model=object(),
                         open_wb=_fake_open({"mes": MES_GRID, "iqc": IQC_GRID}))

    assert summary.status == "ok"
    assert not any("PQC" in p for p in summary.files)

    conn = db.connect(env.resolved_molds_db_path)
    paths = [r["path"] for r in conn.execute("SELECT path FROM ingested_file")]
    conn.close()
    assert not any("PQC" in p for p in paths)


def test_unreadable_file_in_unprocessed_dir_does_not_block_batch(env, monkeypatch):
    """읽지 않는 폴더의 잠긴 파일 때문에 배치가 멈추면 안 된다 —
    그 데이터는 애초에 이번 단계가 쓰지 않는다."""
    import os
    pqc_dir = os.path.join(env.resolved_ingest_root, "PQC")
    os.makedirs(pqc_dir, exist_ok=True)
    with open(os.path.join(pqc_dir, "pqc.xlsx"), "wb") as f:
        f.write(b"pqc")

    with_pqc = env.model_copy(
        update={"ingest_stage_dirs": "MES:mes,IQC:iqc,PQC:pqc"}
    )
    monkeypatch.setattr(
        "app.ingest.pipeline.discover_layout",
        _fake_discover({"mes": MES_LAYOUT, "iqc": IQC_LAYOUT}),
    )
    _lock(monkeypatch, "PQC")

    summary = run_ingest(with_pqc, model=object(),
                         open_wb=_fake_open({"mes": MES_GRID, "iqc": IQC_GRID}))

    assert summary.status == "ok"
    assert summary.unreadable_files == []
