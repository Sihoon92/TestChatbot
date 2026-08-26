"""안정화·오염 판정 — 겹친 조정을 걸러내지 못하면 인과를 분리할 수 없고
영향행렬이 예외 없이 틀린 값으로 수렴한다."""
import pandas as pd

from app.coating import events
from app.coating import schemas as S


def _wm(times, values, lot="L1"):
    return pd.DataFrame({
        S.LOT: [lot] * len(times),
        S.AT: pd.to_datetime(times),
        S.WET_MEAN: values,
    })


def _ev(times, lot="L1"):
    return pd.DataFrame({
        S.LOT: [lot] * len(times),
        S.EVENT: [f"{lot}#{i+1}" for i in range(len(times))],
        S.AT: pd.to_datetime(times),
        "n_items": [1] * len(times),
    })


def test_settles_when_moving_std_drops_below_threshold():
    times = [f"2026-01-31 19:{m:02d}" for m in range(0, 10)]
    # 19:00 조정 → 19:01~19:03 과도 → 19:04 부터 평평
    vals = [18.2, 18.5, 18.4, 18.35, 18.30, 18.30, 18.30, 18.30, 18.30, 18.30]
    out = events.annotate_settling(
        _ev(["2026-01-31 19:00"]), _wm(times, vals),
        std_max=0.02, window_minutes=3, max_wait_minutes=30,
    )
    assert not out.iloc[0][S.CONTAMINATED]
    assert out.iloc[0][S.SETTLED_AT] <= pd.Timestamp("2026-01-31 19:07")


def test_contaminated_when_next_event_arrives_before_settling():
    """재안정 전에 다음 조정이 오면 두 변경의 효과가 겹친다."""
    times = [f"2026-01-31 19:{m:02d}" for m in range(0, 12)]
    vals = [18.2, 18.5, 18.45, 18.4, 18.35, 18.3, 18.3, 18.3, 18.3, 18.3, 18.3, 18.3]
    out = events.annotate_settling(
        _ev(["2026-01-31 19:00", "2026-01-31 19:02"]), _wm(times, vals),
        std_max=0.02, window_minutes=3, max_wait_minutes=30,
    )
    assert bool(out.iloc[0][S.CONTAMINATED]) is True
    assert out.iloc[0][S.DROP_REASON] == "overlapped"


def test_contaminated_when_never_settles_within_max_wait():
    times = [f"2026-01-31 19:{m:02d}" for m in range(0, 8)]
    vals = [18.2, 18.6, 18.1, 18.7, 18.0, 18.8, 18.1, 18.6]
    out = events.annotate_settling(
        _ev(["2026-01-31 19:00"]), _wm(times, vals),
        std_max=0.02, window_minutes=3, max_wait_minutes=5,
    )
    assert bool(out.iloc[0][S.CONTAMINATED]) is True
    assert out.iloc[0][S.DROP_REASON] == "no_settle"


def test_contamination_does_not_leak_across_lots():
    """다른 lot 의 이벤트가 오염 원인이 되면 안 된다."""
    times = [f"2026-01-31 19:{m:02d}" for m in range(0, 10)]
    flat = [18.3] * 10
    ev = pd.concat([_ev(["2026-01-31 19:00"], "L1"), _ev(["2026-01-31 19:01"], "L2")])
    wm = pd.concat([_wm(times, flat, "L1"), _wm(times, flat, "L2")])
    out = events.annotate_settling(
        ev, wm, std_max=0.02, window_minutes=3, max_wait_minutes=30
    )
    assert not out[S.CONTAMINATED].any()
