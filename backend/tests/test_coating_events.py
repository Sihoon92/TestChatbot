"""조정 이벤트 — 묶음이 틀리면 하나의 Wet 변화가 여러 이벤트에 중복
귀속되어 각 zone 의 영향이 부풀려진다."""
import pandas as pd

from app.coating import events
from app.coating import schemas as S


def _changes(rows):
    return pd.DataFrame(
        [
            {S.LOT: r[0], S.ITEM: r[1], S.AT: pd.Timestamp(r[2]), S.VALUE: r[3], S.PREV_VALUE: r[4]}
            for r in rows
        ]
    )


def test_simultaneous_changes_become_one_event():
    """작업자는 여러 zone 을 한 번에 만진다. 항목별로 쪼개면 안 된다."""
    c = _changes([
        ("L1", "30030838", "2026-01-31 19:00", 305.0, 300.0),
        ("L1", "30030839", "2026-01-31 19:01", 402.0, 400.0),
    ])
    ev, dl = events.build_events(c, merge_minutes=2)
    assert len(ev) == 1
    assert ev.iloc[0]["n_items"] == 2
    assert set(dl[S.DELTA]) == {5.0, 2.0}


def test_changes_beyond_window_become_separate_events():
    c = _changes([
        ("L1", "30030838", "2026-01-31 19:00", 305.0, 300.0),
        ("L1", "30030838", "2026-01-31 19:10", 310.0, 305.0),
    ])
    ev, _ = events.build_events(c, merge_minutes=2)
    assert len(ev) == 2


def test_initial_values_are_not_events():
    """lot 시작값은 '사람이 바꾼 것' 이 아니다. 이걸 이벤트로 세면
    모든 lot 이 25개짜리 가짜 이벤트를 하나씩 갖게 된다."""
    c = _changes([("L1", "30030838", "2026-01-31 18:55", 300.0, float("nan"))])
    ev, dl = events.build_events(c, merge_minutes=2)
    assert len(ev) == 0
    assert len(dl) == 0


def test_output_items_are_not_events():
    """Wet 은 측정값이다. 측정 변화를 조정으로 세면 인과가 뒤집힌다."""
    c = _changes([("L1", "90030611", "2026-01-31 19:00", 18.3, 18.2)])
    ev, _ = events.build_events(c, merge_minutes=2)
    assert len(ev) == 0


def test_events_do_not_span_lots():
    c = _changes([
        ("L1", "30030838", "2026-01-31 19:00", 305.0, 300.0),
        ("L2", "30030838", "2026-01-31 19:01", 305.0, 300.0),
    ])
    ev, _ = events.build_events(c, merge_minutes=5)
    assert len(ev) == 2


def test_zone_is_attached_to_gap_deltas():
    """영향행렬은 zone 인덱스로 조립된다. zone 이 없으면 조립 불가."""
    c = _changes([("L1", "30030843", "2026-01-31 19:00", 316.0, 315.0)])
    _, dl = events.build_events(c, merge_minutes=2)
    assert dl.iloc[0][S.ZONE] == 6
