"""필터·조회 로직 검증.

스텁이라도 필터링을 실제로 구현한다 — 고정 JSON 을 그대로 돌려주면 프론트의
필터 UX 를 검증할 수 없다. 이후 서브프로젝트에서 이 모듈 내부만 DB 쿼리로
바뀌므로, 여기 테스트가 그때의 회귀 방지선이 된다.
"""
from app.molds.query import filter_options, get_mold, list_molds


def test_no_filter_returns_all():
    assert len(list_molds()) == 4


def test_filter_by_status():
    result = list_molds(status="in_use")
    assert {m.mold_no for m in result} == {"M-1024", "M-1031"}


def test_filter_by_line_and_machine():
    result = list_molds(status="in_use", line="3", machine="2")
    assert [m.mold_no for m in result] == ["M-1024"]


def test_filter_by_line_only():
    result = list_molds(line="3")
    assert {m.mold_no for m in result} == {"M-1024", "M-1031"}


def test_search_is_partial_match():
    result = list_molds(q="10")
    assert {m.mold_no for m in result} == {"M-1024", "M-1031"}


def test_search_ignores_case_and_surrounding_space():
    assert [m.mold_no for m in list_molds(q="  m-1024 ")] == ["M-1024"]


def test_search_no_match_returns_empty():
    assert list_molds(q="ZZZ") == []


def test_impossible_combination_returns_empty():
    """대기중 금형은 호기가 없으므로 이 조합은 항상 0건이다."""
    assert list_molds(status="standby", line="3") == []


def test_get_mold_returns_detail():
    mold = get_mold("M-1024")
    assert mold is not None
    assert mold.summary.mold_no == "M-1024"
    assert len(mold.productions) == 3


def test_get_mold_unknown_returns_none():
    assert get_mold("M-9999") is None


def test_filter_options_statuses_are_fixed_four():
    """상태는 도메인 어휘다. 데이터에 없는 '폐기'도 조회 수단은 남아야 한다."""
    assert filter_options().statuses == ["in_use", "standby", "repair", "retired"]


def test_filter_options_installations_are_existing_pairs_only():
    pairs = {(i.line, i.machine) for i in filter_options().installations}
    assert pairs == {("3", "2"), ("3", "5")}


def test_filter_options_installations_have_no_duplicates():
    installations = filter_options().installations
    pairs = [(i.line, i.machine) for i in installations]
    assert len(pairs) == len(set(pairs))
