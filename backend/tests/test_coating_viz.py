"""그래프 조립 — 여기가 틀리면 화면이 조용히 거짓말을 한다.

viz 는 streamlit 을 import 하지 않으므로 평범한 단위 테스트가 된다. dashboard 는
위젯만 달려 있어서 스모크만 건다(맨 아래).
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.coating import parse, viz
from app.coating import schemas as S

SAMPLE = Path(__file__).parent / "fixtures" / "coating" / "sample_long.csv"

GAP2, GAP3 = S.GAP_ITEM_IDS[1], S.GAP_ITEM_IDS[2]
WET2 = S.WET_ITEM_IDS[1]
RPM = "50030111"


def make(rows) -> pd.DataFrame:
    """(분, item_id, value) 목록 → readings 모양. 사전은 실제 것을 조인한다."""
    df = pd.DataFrame(
        [
            {S.LOT: "L1", S.AT: pd.Timestamp("2026-01-31 18:00") + pd.Timedelta(minutes=m),
             S.PRODUCT: "BNB48X1", S.ITEM: item, S.VALUE: float(v)}
            for m, item, v in rows
        ]
    )
    df[S.ROW_NO] = range(len(df))
    return df.merge(parse.load_item_dictionary(), on=S.ITEM, how="left")


# ── 그룹 배치 ───────────────────────────────────────────────────────────

def test_groups_split_input_zone_scalar_and_output():
    """단위가 다른 계열을 한 축에 올리면 작은 쪽이 바닥에 눌린다."""
    assert viz.group_of(GAP2) == viz.INPUT_ZONE
    assert viz.group_of(RPM) == viz.INPUT_SCALAR
    assert viz.group_of(WET2) == viz.OUTPUT


def test_unknown_item_gets_no_group():
    """사전에 없는 항목은 어디에 그릴지 모른다. 추측하지 않는다."""
    assert viz.group_of("99999999") is None


def test_selected_items_land_on_separate_rows():
    r = make([(0, GAP2, 300), (0, RPM, 12), (0, WET2, 18.2)])
    fig = viz.timeseries_figure(r, [GAP2, RPM, WET2])
    rows = {t.yaxis for t in fig.data}
    assert len(rows) == 3
    assert [a.text for a in fig.layout.annotations] == list(viz.GROUP_ORDER)


def test_empty_group_gets_no_row():
    """선택이 없는 그룹까지 행을 만들면 빈 칸이 화면을 먹는다."""
    r = make([(0, GAP2, 300), (0, WET2, 18.2)])
    fig = viz.timeseries_figure(r, [GAP2])
    assert [a.text for a in fig.layout.annotations] == [viz.INPUT_ZONE]


def test_rows_share_the_time_axis():
    """입력을 바꾼 순간과 출력이 따라온 순간을 같은 x 위치에서 읽어야 한다."""
    r = make([(0, GAP2, 300), (0, WET2, 18.2)])
    fig = viz.timeseries_figure(r, [GAP2, WET2])
    # 위 행의 x축이 아래 행 것을 따라간다(shared_xaxes 의 결과).
    assert fig.layout.xaxis.matches == "x2" or fig.layout.xaxis2.matches == "x"


def test_only_selected_items_are_drawn():
    r = make([(0, GAP2, 300), (0, GAP3, 310), (0, WET2, 18.2)])
    fig = viz.timeseries_figure(r, [GAP2, WET2])
    assert len(fig.data) == 2
    assert all("3Zone" not in t.name for t in fig.data)


# ── 거짓말을 막는 두 규칙 ───────────────────────────────────────────────

def test_zero_wet_is_treated_as_missing_not_as_a_value():
    """Wet 의 0 은 '미사용/미측정' 이다(features.wet_wide 와 같은 규칙).
    값으로 그리면 18.2 에서 0 으로 떨어지는 절벽이 생겨 사고처럼 보인다."""
    r = make([(0, WET2, 18.2), (1, WET2, 0.0), (2, WET2, 18.3)])
    fig = viz.timeseries_figure(r, [WET2])
    y = list(fig.data[0].y)
    assert np.isnan(y[1]), f"0 이 값으로 남았다: {y}"
    assert fig.data[0].connectgaps is False   # 결측을 이어 붙이지도 않는다


def test_zero_input_is_kept_as_a_real_value():
    """입력의 0 은 진짜 0 이다. 출력 규칙을 입력에 적용하면 데이터가 사라진다."""
    r = make([(0, GAP2, 0.0), (1, GAP2, 300)])
    fig = viz.timeseries_figure(r, [GAP2])
    assert list(fig.data[0].y)[0] == 0.0


def test_lines_are_stepped_not_interpolated():
    """원본은 스냅샷 로그라 값이 계단형이다. 직선 보간은 없는 변화를 그린다."""
    r = make([(0, GAP2, 300), (5, GAP2, 320)])
    fig = viz.timeseries_figure(r, [GAP2])
    assert fig.data[0].line.shape == "hv"


# ── 항목 목록과 기본 선택 ───────────────────────────────────────────────

def test_available_items_lists_only_what_is_present():
    r = make([(0, GAP2, 300), (0, WET2, 18.2)])
    assert set(viz.available_items(r)[S.ITEM]) == {GAP2, WET2}


def test_available_items_uses_the_human_name():
    """item_id 를 그대로 보여주면 사람이 고를 수 없다."""
    r = make([(0, GAP2, 300)])
    assert "2Zone" in viz.available_items(r)["label"].iloc[0]


def test_changed_only_narrows_the_list():
    r = make([(0, GAP2, 300), (1, GAP2, 320), (0, GAP3, 310), (1, GAP3, 310)])
    assert set(viz.available_items(r, changed_only=True)[S.ITEM]) == {GAP2}


def test_default_items_prefer_the_ones_that_moved():
    """전부 켜면 54계열이라 못 읽는다. 안 변한 항목은 직선이라 볼 것이 없다."""
    r = make([(0, GAP2, 300), (1, GAP2, 320), (0, GAP3, 310), (1, GAP3, 310)])
    assert viz.default_items(r) == [GAP2]


def test_default_items_fall_back_when_nothing_moved():
    """안정 구간만 있는 데이터에서 빈 화면을 주면 안 된다."""
    r = make([(0, GAP2, 300), (1, GAP2, 300), (0, GAP3, 310)])
    assert viz.default_items(r)


def test_default_items_are_capped():
    rows = [(m, item, 300 + m) for item in S.GAP_ITEM_IDS[:12] for m in (0, 1)]
    assert len(viz.default_items(make(rows), limit=8)) == 8


# ── 조정 이벤트 ─────────────────────────────────────────────────────────

def _events(times, contaminated=None) -> pd.DataFrame:
    ev = pd.DataFrame({
        S.LOT: "L1",
        S.EVENT: [f"L1#{i}" for i in range(len(times))],
        S.AT: [pd.Timestamp("2026-01-31 18:00") + pd.Timedelta(minutes=m) for m in times],
    })
    if contaminated is not None:
        ev[S.CONTAMINATED] = contaminated
    return ev


def test_events_are_drawn_on_every_row():
    """입력과 출력에 같은 세로선이 있어야 반응 지연이 눈으로 보인다."""
    r = make([(0, GAP2, 300), (3, GAP2, 320), (0, WET2, 18.2), (3, WET2, 18.4)])
    fig = viz.timeseries_figure(r, [GAP2, WET2], _events([3]))
    assert len(fig.layout.shapes) == 2      # 2행 × 이벤트 1건


def test_contaminated_events_are_a_different_colour():
    """인과 분리가 안 되는 이벤트를 같은 선으로 그리면 잘못 읽는다."""
    r = make([(0, GAP2, 300), (3, GAP2, 320)])
    fig = viz.timeseries_figure(r, [GAP2], _events([1, 3], [False, True]))
    colours = {s.line.color for s in fig.layout.shapes}
    assert len(colours) == 2


def test_no_events_is_not_an_error():
    r = make([(0, GAP2, 300)])
    fig = viz.timeseries_figure(r, [GAP2], pd.DataFrame())
    assert not fig.layout.shapes


# ── 가장자리 ────────────────────────────────────────────────────────────

def test_no_selection_gives_a_placeholder_not_a_crash():
    fig = viz.timeseries_figure(make([(0, GAP2, 300)]), [])
    assert not fig.data
    assert "선택" in fig.layout.annotations[0].text


def test_empty_readings_is_not_an_error():
    empty = make([(0, GAP2, 300)]).iloc[0:0]
    assert viz.available_items(empty).empty
    assert viz.default_items(empty) == []


def test_real_fixture_builds_a_figure():
    """합성 데이터만 보면 실제 컬럼 구성이 달라도 모른다."""
    r = parse.load_readings(SAMPLE)
    fig = viz.timeseries_figure(r, viz.default_items(r))
    assert fig.data


# ── dashboard 스모크 ────────────────────────────────────────────────────

def test_dashboard_renders_a_chart():
    """위젯 배선이 깨졌는지만 본다. 판단은 전부 위에서 검증했다.

    경로를 픽스처로 직접 지정하는 것이 중요하다. 기본 경로(COATING_INPUT_PATH)는
    gitignore 대상인 backend/data/ 아래라, 새로 클론한 곳에서는 파일이 없어
    st.stop() 으로 조용히 끝난다 - 그러면 예외가 없으니 이 테스트는 아무것도
    검증하지 않으면서 통과한다.
    """
    at = pytest.importorskip("streamlit.testing.v1").AppTest
    app = at.from_file(str(Path(__file__).parents[1] / "app" / "coating" / "dashboard.py"))
    app.run(timeout=60)
    app.text_input[0].set_value(str(SAMPLE)).run(timeout=60)

    assert not app.exception
    assert not app.error
    assert len(app.get("plotly_chart")) == 1


def test_switching_files_does_not_keep_a_stale_selection(tmp_path):
    """위젯 key 에 경로를 넣지 않으면 다른 파일을 열어도 옛 선택이 session_state
    에 남는다. 새 파일에 없는 항목은 조용히 떨어져 나가고, 남은 몇 개만 그려져
    "출력이 통째로 비어 보이는" 화면이 된다. 실제로 겪어서 넣은 회귀다."""
    at = pytest.importorskip("streamlit.testing.v1").AppTest

    other = tmp_path / "다른항목.csv"
    other.write_text(
        "lot_id,worked_at,product,item_id,value\n"
        + "".join(
            f"L9,2026-02-01 09:0{m},P1,{item},{v}\n"
            for m in (0, 1)
            for item, v in ((S.GAP_ITEM_IDS[0], 300 + m), (S.WET_ITEM_IDS[0], 18.2 + m / 10))
        ),
        encoding="utf-8-sig",
    )

    app = at.from_file(str(Path(__file__).parents[1] / "app" / "coating" / "dashboard.py"))
    app.run(timeout=60)
    app.text_input[0].set_value(str(SAMPLE)).run(timeout=60)
    app.text_input[0].set_value(str(other)).run(timeout=60)

    assert not app.exception
    # 새 파일의 기본값(값이 변한 gap 1개 + wet 1개)이 적용돼야 한다.
    assert sum(len(m.value) for m in app.multiselect) == 2


def test_dashboard_shows_the_read_error_instead_of_a_traceback(tmp_path):
    """DRM·인코딩·헤더 불일치는 parse 가 이미 원인과 대처를 문장으로 만들어 둔다.
    그걸 안 받으면 streamlit 이 트레이스백을 띄워 그 문장이 스택 밑에 묻힌다 —
    설정 문제가 버그처럼 보인다. 실제로 그렇게 나와서 넣은 회귀다."""
    at = pytest.importorskip("streamlit.testing.v1").AppTest

    drm = tmp_path / "보안.csv"
    drm.write_bytes(b"<## NASCA DRM FILE ##>" + bytes(range(200, 256)))

    app = at.from_file(str(Path(__file__).parents[1] / "app" / "coating" / "dashboard.py"))
    app.run(timeout=60)
    app.text_input[0].set_value(str(drm)).run(timeout=60)

    assert not app.exception, "트레이스백이 새어 나왔다"
    assert "DRM" in app.error[0].value          # 원인
    assert "convert" in app.info[0].value       # 대처


def test_dashboard_stops_with_a_message_on_a_bad_path(tmp_path):
    """경로를 잘못 적었을 때 트레이스백이 아니라 문장이 나와야 한다."""
    at = pytest.importorskip("streamlit.testing.v1").AppTest
    app = at.from_file(str(Path(__file__).parents[1] / "app" / "coating" / "dashboard.py"))
    app.run(timeout=60)
    app.text_input[0].set_value(str(tmp_path / "없는파일.parquet")).run(timeout=60)

    assert not app.exception
    assert "없는파일.parquet" in app.error[0].value
