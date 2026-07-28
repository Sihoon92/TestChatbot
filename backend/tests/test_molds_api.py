"""라우터 배선 검증.

이 테스트의 관심사는 라우터 계약(경로 선언 순서, 404 처리, 직렬화)이지
데이터 출처(샘플/DB)가 아니다. 그래서 `query` 함수를 샘플 데이터로 스텁해
DB/settings 없이 `TestClient` 로 가볍게 검증한다.
"""
import pytest
from fastapi.testclient import TestClient

# app.main 을 먼저 로드해야 한다(라우터 모듈이 app.main 을 import 하지 않더라도,
# 다른 테스트와 동일한 import 순서를 유지해 순환 import 를 피한다).
import app.main  # noqa: E402

client = TestClient(app.main.app)


@pytest.fixture(autouse=True)
def _stub_query(monkeypatch):
    """라우터 계약만 검증한다. 데이터 출처(샘플/DB)는 이 테스트의 관심사가 아니다."""
    from app.molds import sample_data
    from app.molds.schemas import ALL_STATUSES, FilterOptions, Installation

    def _fake_list_molds(*, status=None, line=None, machine=None, q=None):
        result = [m.summary for m in sample_data.SAMPLE_MOLDS]
        if status is not None:
            result = [s for s in result if s.status == status]
        if line is not None:
            result = [s for s in result if s.line == line]
        if machine is not None:
            result = [s for s in result if s.machine == machine]
        if q is not None:
            needle = q.strip().lower()
            if needle:
                result = [s for s in result if needle in s.mold_no.lower()]
        return result

    monkeypatch.setattr("app.api.molds.list_molds", _fake_list_molds)
    monkeypatch.setattr(
        "app.api.molds.get_mold",
        lambda no: next(
            (m for m in sample_data.SAMPLE_MOLDS if m.summary.mold_no == no), None
        ),
    )

    def _fake_filter_options() -> FilterOptions:
        seen: list[tuple[str, str]] = []
        for mold in sample_data.SAMPLE_MOLDS:
            line, machine = mold.summary.line, mold.summary.machine
            if line is None or machine is None:
                continue
            if (line, machine) not in seen:
                seen.append((line, machine))
        seen.sort()
        return FilterOptions(
            statuses=list(ALL_STATUSES),
            installations=[Installation(line=ln, machine=mc) for ln, mc in seen],
        )

    monkeypatch.setattr("app.api.molds.filter_options", _fake_filter_options)


def test_list_returns_all_molds():
    res = client.get("/api/molds")
    assert res.status_code == 200
    assert len(res.json()) == 4


def test_list_applies_status_filter():
    res = client.get("/api/molds", params={"status": "in_use"})
    assert res.status_code == 200
    assert {m["mold_no"] for m in res.json()} == {"RX28312", "RX28315"}


def test_list_applies_installation_filter():
    res = client.get("/api/molds", params={"status": "in_use", "line": "3", "machine": "2"})
    assert [m["mold_no"] for m in res.json()] == ["RX28312"]


def test_list_applies_search():
    res = client.get("/api/molds", params={"q": "411"})
    assert [m["mold_no"] for m in res.json()] == ["RX41194"]


def test_list_rejects_unknown_status():
    """오타 난 상태값이 조용히 무시돼 '전체 목록'이 되면 안 된다."""
    res = client.get("/api/molds", params={"status": "running"})
    assert res.status_code == 422


def test_detail_returns_full_payload():
    res = client.get("/api/molds/RX28312")
    assert res.status_code == 200
    body = res.json()
    assert body["summary"]["mold_no"] == "RX28312"
    assert len(body["productions"]) == 3
    assert {s["stage"] for s in body["stages"]} == {"iqc", "pqc", "ai_recheck"}


def test_detail_unknown_mold_is_404():
    res = client.get("/api/molds/M-9999")
    assert res.status_code == 404


def test_filters_endpoint_is_not_shadowed_by_detail_route():
    """/molds/filters 가 /molds/{mold_no} 로 잡히면 404 가 난다 — 라우트 순서 회귀 방지."""
    res = client.get("/api/molds/filters")
    assert res.status_code == 200
    body = res.json()
    assert body["statuses"] == ["in_use", "standby", "repair", "retired"]
    assert {(i["line"], i["machine"]) for i in body["installations"]} == {("3", "2"), ("3", "5")}


def test_detail_null_fields_survive_serialization():
    """null 이 0/빈문자열로 바뀌면 화면이 '미상'과 '0'을 구분할 수 없게 된다."""
    body = client.get("/api/molds/RX41194").json()
    assert body["design"]["angle_deg"] is None
    assert body["summary"]["line"] is None
    assert body["current"]["installed_at"] is None


def test_detail_null_quantity_fields_survive_serialization():
    """이번 태스크에서 nullable 로 바꾼 다섯 수량 필드가 실제로 API 응답에서
    null 로 직렬화되는지 확인한다. `v or 0` 류 기본값 로직이 나중에 끼어들면
    이 테스트가 잡아야 한다. RX39002 는 수량이 전부 None 인 샘플이다."""
    body = client.get("/api/molds/RX39002").json()
    assert body["summary"]["shot_count"] is None
    assert body["summary"]["total_production"] is None
    assert body["history"]["total_installs"] is None
    assert body["history"]["total_production"] is None
    assert body["current"]["shot_count"] is None
