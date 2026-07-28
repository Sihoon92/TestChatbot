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

    run_ingest(env, model=object(), open_wb=opener)
    assert calls["n"] == 2, "앵커가 같으면 에이전트를 다시 부르지 않아야 한다"


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
