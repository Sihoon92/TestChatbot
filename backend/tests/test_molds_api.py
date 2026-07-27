"""라우터 배선 검증.

이 라우터는 의도적으로 `Depends(get_app_state)` 를 쓰지 않는다 — 샘플 데이터가
코드 안에 있어 settings/DB 가 필요 없고, 덕분에 lifespan 을 띄우지 않는
TestClient 로 가볍게 검증할 수 있다.
"""
from fastapi.testclient import TestClient

# app.main 을 먼저 로드해야 한다(라우터 모듈이 app.main 을 import 하지 않더라도,
# 다른 테스트와 동일한 import 순서를 유지해 순환 import 를 피한다).
import app.main  # noqa: E402

client = TestClient(app.main.app)


def test_list_returns_all_molds():
    res = client.get("/api/molds")
    assert res.status_code == 200
    assert len(res.json()) == 4


def test_list_applies_status_filter():
    res = client.get("/api/molds", params={"status": "in_use"})
    assert res.status_code == 200
    assert {m["mold_no"] for m in res.json()} == {"M-1024", "M-1031"}


def test_list_applies_installation_filter():
    res = client.get("/api/molds", params={"status": "in_use", "line": "3", "machine": "2"})
    assert [m["mold_no"] for m in res.json()] == ["M-1024"]


def test_list_applies_search():
    res = client.get("/api/molds", params={"q": "0998"})
    assert [m["mold_no"] for m in res.json()] == ["M-0998"]


def test_list_rejects_unknown_status():
    """오타 난 상태값이 조용히 무시돼 '전체 목록'이 되면 안 된다."""
    res = client.get("/api/molds", params={"status": "running"})
    assert res.status_code == 422


def test_detail_returns_full_payload():
    res = client.get("/api/molds/M-1024")
    assert res.status_code == 200
    body = res.json()
    assert body["summary"]["mold_no"] == "M-1024"
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
    body = client.get("/api/molds/M-0998").json()
    assert body["design"]["angle_deg"] is None
    assert body["summary"]["line"] is None
    assert body["current"]["installed_at"] is None
