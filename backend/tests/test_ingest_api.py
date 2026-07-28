"""수집 API. lifespan 없이 TestClient 를 써서 LLM/DB 초기화에 묶이지 않게 한다."""
import pytest
from fastapi.testclient import TestClient

from app.ingest.schemas import RunSummary
from app.main import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("MOLDS_DB_PATH", str(tmp_path / "molds.db"))
    monkeypatch.setenv("INGEST_ROOT", str(tmp_path / "uploads"))
    from app.config import get_settings
    get_settings.cache_clear()
    yield TestClient(create_app())
    get_settings.cache_clear()


def test_status_is_null_before_first_run(client):
    res = client.get("/api/ingest/status")
    assert res.status_code == 200
    assert res.json() is None


def test_run_returns_summary(client, monkeypatch):
    called = {}

    def _fake_run(settings, model=None, *, open_wb=None, config=None):
        called["yes"] = True
        return RunSummary(status="ok", started_at="2026-07-28T00:00:00",
                          mold_count=2, iqc_matched=1)

    monkeypatch.setattr("app.api.ingest.run_ingest", _fake_run)
    # 실제 LLM 을 만들지 않는다 — 수집 트리거 테스트가 LLM 연결에 묶이면 안 된다.
    monkeypatch.setattr("app.api.ingest.get_chat_model", lambda s: object())

    res = client.post("/api/ingest/run")

    assert res.status_code == 200
    assert res.json()["mold_count"] == 2
    assert called["yes"]


def test_run_reports_error_as_200_summary_not_500(monkeypatch, client):
    """수집 실패는 서버 오류가 아니라 배치 결과다. 500 으로 던지면 화면이
    사유를 보여주지 못하고 '알 수 없는 오류'만 남는다."""
    def _boom(settings, model=None, *, open_wb=None, config=None):
        return RunSummary(status="error", started_at="2026-07-28T00:00:00",
                          error="MES 파일이 없다")

    monkeypatch.setattr("app.api.ingest.run_ingest", _boom)
    monkeypatch.setattr("app.api.ingest.get_chat_model", lambda s: object())

    res = client.post("/api/ingest/run")

    assert res.status_code == 200
    assert res.json()["status"] == "error"
    assert "MES" in res.json()["error"]
