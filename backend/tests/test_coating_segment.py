"""튜닝 구간 절단 — 경계가 오염되면 초기 구간과 정상 구간이 섞인다."""
import pandas as pd

from app.coating import schemas as S
from app.coating import segment


def _changes(rows):
    return pd.DataFrame(
        [{S.LOT: r[0], S.ITEM: r[1], S.AT: pd.Timestamp(r[2]), S.VALUE: r[3]} for r in rows]
    )


def test_tuning_end_is_last_control_change_not_last_observation():
    """'사람이 손 뗀 시점' 이 1차 기준이다. 마지막 관측 시각을 쓰면 lot 전체가
    튜닝 구간이 되어버린다."""
    c = _changes([
        ("L1", "30030838", "2026-01-31 18:55", 300.0),
        ("L1", "30030838", "2026-01-31 19:10", 305.0),
        ("L1", "90030611", "2026-01-31 19:40", 18.2),  # 출력은 제어가 아니다
    ])
    out = segment.tuning_end_last_change(c)
    assert out.loc[out[S.LOT] == "L1", "tuning_end"].iloc[0] == pd.Timestamp("2026-01-31 19:10")


def test_tuning_end_is_nat_when_nothing_was_touched():
    """시작값만 있고 변경이 없으면 튜닝 이벤트가 없다. 이런 lot 은
    영향행렬 학습에 기여하지 못하며, 그 비율 자체가 리포트 항목이다."""
    c = _changes([("L1", "30030838", "2026-01-31 18:55", 300.0)])
    out = segment.tuning_end_last_change(c)
    assert pd.isna(out.loc[out[S.LOT] == "L1", "tuning_end"].iloc[0])


def test_band_entry_requires_consecutive_observations():
    """한 점이 우연히 밴드에 들어온 것과 정착한 것은 다르다."""
    wm = pd.DataFrame({
        S.LOT: ["L1"] * 5,
        S.AT: pd.to_datetime([
            "2026-01-31 18:55", "2026-01-31 18:56", "2026-01-31 18:57",
            "2026-01-31 18:58", "2026-01-31 18:59",
        ]),
        S.WET_MEAN: [17.0, 18.23, 17.0, 18.24, 18.22],
    })
    out = segment.tuning_end_band(wm, target=18.23, band=0.1, min_consecutive=2)
    assert out.loc[out[S.LOT] == "L1", "band_entry"].iloc[0] == pd.Timestamp("2026-01-31 18:58")


def test_band_entry_is_nat_when_never_settles():
    wm = pd.DataFrame({
        S.LOT: ["L1"] * 3,
        S.AT: pd.to_datetime(["2026-01-31 18:55", "2026-01-31 18:56", "2026-01-31 18:57"]),
        S.WET_MEAN: [17.0, 17.1, 17.2],
    })
    out = segment.tuning_end_band(wm, target=18.23, band=0.1, min_consecutive=2)
    assert pd.isna(out.loc[out[S.LOT] == "L1", "band_entry"].iloc[0])
