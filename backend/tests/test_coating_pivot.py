"""계단형 값의 압축 — 이 모듈이 틀리면 조정 이벤트가 통째로 사라지거나
없던 이벤트가 생긴다."""
import pandas as pd

from app.coating import pivot
from app.coating import schemas as S


def _readings(rows):
    """(lot, 분, item, value, row_no) 튜플로 최소 readings 를 만든다."""
    return pd.DataFrame(
        [
            {
                S.LOT: r[0],
                S.AT: pd.Timestamp(r[1]),
                S.ITEM: r[2],
                S.VALUE: r[3],
                S.ROW_NO: i,
                S.IO: S.IO_INPUT,
            }
            for i, r in enumerate(rows)
        ]
    )


def test_dedupe_minute_takes_last_row_not_first():
    """같은 분에 두 번 찍히면 나중 것이 현재 상태다. 첫 값을 취하면
    변경이 한 분씩 밀린다."""
    r = _readings([
        ("L1", "2026-01-31 18:56", "A", 10.0),
        ("L1", "2026-01-31 18:56", "A", 11.0),
    ])
    d = pivot.dedupe_minute(r)
    assert len(d) == 1
    assert d.iloc[0][S.VALUE] == 11.0


def test_dedupe_minute_keeps_other_items_and_minutes():
    r = _readings([
        ("L1", "2026-01-31 18:56", "A", 10.0),
        ("L1", "2026-01-31 18:56", "B", 20.0),
        ("L1", "2026-01-31 18:57", "A", 10.0),
    ])
    assert len(pivot.dedupe_minute(r)) == 3


def test_compress_runs_keeps_only_changes_plus_initial():
    """값이 유지되는 동안은 행이 없어야 한다. 유지값을 다 남기면
    '변경 횟수' 가 로깅 횟수로 부풀려져 식별성 판정이 완전히 틀어진다."""
    r = _readings([
        ("L1", "2026-01-31 18:55", "A", 10.0),
        ("L1", "2026-01-31 18:56", "A", 10.0),
        ("L1", "2026-01-31 18:57", "A", 11.0),
        ("L1", "2026-01-31 18:58", "A", 11.0),
        ("L1", "2026-01-31 18:59", "A", 12.0),
    ])
    c = pivot.compress_runs(pivot.dedupe_minute(r))
    assert list(c[S.AT].dt.strftime("%H:%M")) == ["18:55", "18:57", "18:59"]
    assert list(c[S.VALUE]) == [10.0, 11.0, 12.0]
    assert pd.isna(c.iloc[0][S.PREV_VALUE])
    assert c.iloc[1][S.PREV_VALUE] == 10.0


def test_compress_runs_does_not_bridge_lots():
    """lot 이 바뀌면 시작값이 다시 시작이다. lot 을 가로질러 비교하면
    job change 자체가 변경 이벤트로 잡힌다."""
    r = _readings([
        ("L1", "2026-01-31 18:55", "A", 10.0),
        ("L2", "2026-01-31 19:55", "A", 10.0),
    ])
    c = pivot.compress_runs(pivot.dedupe_minute(r))
    assert len(c) == 2
    assert pd.isna(c[S.PREV_VALUE]).all()


def test_state_at_forward_fills():
    """임의 시각의 전체 상태를 복원한다. 변경 이전 시각이면 그 항목은 없다."""
    r = _readings([
        ("L1", "2026-01-31 18:55", "A", 10.0),
        ("L1", "2026-01-31 18:57", "A", 11.0),
        ("L1", "2026-01-31 18:56", "B", 20.0),
    ])
    c = pivot.compress_runs(pivot.dedupe_minute(r))
    assert pivot.state_at(c, "L1", pd.Timestamp("2026-01-31 18:56")) == {"A": 10.0, "B": 20.0}
    assert pivot.state_at(c, "L1", pd.Timestamp("2026-01-31 18:58")) == {"A": 11.0, "B": 20.0}
    assert pivot.state_at(c, "L1", pd.Timestamp("2026-01-31 18:55")) == {"A": 10.0}
