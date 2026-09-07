"""커널 진단 — 리포트가 "물리로 보기 어렵다" 를 냈을 때 그것이 데이터 탓인지
코드 탓인지를 가르는 도구다. 여기가 틀리면 엉뚱한 곳을 파게 된다."""
import numpy as np
import pandas as pd
import pytest

from app.coating import diagnose, features
from app.coating import response as resp
from app.coating import schemas as S
from app.coating.model.profile import build_design


def _aligned(d_gap: float, n_zones: int = 3, lags=range(0, 30)) -> pd.DataFrame:
    """(이벤트, zone) 마다 같은 Δgap 이 lag 축으로 반복된 표 — 실물과 같은 모양."""
    rows = []
    for e in range(4):
        for z in range(1, n_zones + 1):
            for lag in lags:
                rows.append({
                    S.EVENT: f"e{e}", S.ZONE: z, resp.LAG: lag,
                    resp.D_GAP: d_gap, resp.RESPONSE: 0.5,
                })
    return pd.DataFrame(rows)


def _curve(final: float) -> pd.DataFrame:
    lags = list(range(0, 40))
    return pd.DataFrame({
        resp.LAG: lags,
        "mean": [final] * len(lags),
        "sem": [0.001] * len(lags),
        "n": [50] * len(lags),
    })


def _samples(delta_gap: np.ndarray, delta_wet: np.ndarray) -> pd.DataFrame:
    df = pd.DataFrame(delta_gap, columns=features.GAP_DELTA_COLS)
    for i, c in enumerate(features.WET_DELTA_COLS):
        df[c] = delta_wet[:, i]
    return df


def test_expected_center_tap_divides_final_by_typical_gap_move():
    """중심 탭이 있어야 할 크기 = 최종 변화량 ÷ |Δgap| 1회분."""
    out = diagnose.expected_center_tap(_aligned(d_gap=20.0), _curve(final=0.6))
    assert out["median_abs_dgap"] == pytest.approx(20.0)
    assert out["final"] == pytest.approx(0.6)
    assert out["expected"] == pytest.approx(0.03)


def test_expected_center_tap_counts_each_event_zone_once():
    """aligned 는 lag 축으로 같은 Δgap 을 수십 번 담는다. 그대로 세면 관측 창
    길이가 통계에 섞인다 — (이벤트, zone) 마다 한 번이어야 한다."""
    out = diagnose.expected_center_tap(_aligned(d_gap=20.0, n_zones=3), _curve(0.6))
    assert out["n_pairs"] == 4 * 3


def test_expected_center_tap_is_none_without_curve():
    out = diagnose.expected_center_tap(pd.DataFrame(), pd.DataFrame())
    assert out["expected"] is None


def _wide_kernel_samples(seed=0, k_true=4):
    """참 커널이 k=4(9탭)인 데이터. k=2 로 보면 반드시 잘린다."""
    rng = np.random.default_rng(seed)
    true = np.array([0.05, 0.10, 0.18, 0.28, 0.36, 0.28, 0.18, 0.10, 0.05])
    dg = np.zeros((60, S.N_ZONES))
    for e in range(60):
        for z in rng.choice(np.arange(1, 24), size=3, replace=False):
            dg[e, z] = rng.choice([-1, 1]) * rng.uniform(5, 20)
    dw = (build_design(dg, k_true) @ true).reshape(60, S.N_ZONES)
    dw += rng.normal(0, 0.3, (60, 1))
    dw += rng.normal(0, 0.02, (60, S.N_ZONES))
    return dg, dw


def test_sweep_finds_the_k_that_stops_truncating():
    """넓은 커널을 k=2 로 보면 가장자리가 크고, k 를 늘리면 꼬리가 담긴다."""
    dg, dw = _wide_kernel_samples()
    rows = diagnose.sweep_kernels(dg, dw, alpha=1.0)
    by_k = {r["k"]: r for r in rows}
    assert by_k[2]["edge_ratio"] > by_k[5]["edge_ratio"]
    assert by_k[5]["plausible"] is True


def test_sweep_verdict_names_the_setting_to_change():
    """판정이 행동이 되려면 어느 설정을 얼마로 둘지까지 적어야 한다."""
    dg, dw = _wide_kernel_samples()
    text = diagnose._sweep_verdict(diagnose.sweep_kernels(dg, dw, alpha=1.0))
    assert "COATING_KERNEL_HALF_WIDTH" in text


def test_sweep_verdict_says_undecidable_not_wrong_when_underpowered():
    """ΔWet 이 Δgap 과 무관하면 탭이 0 과 구별되지 않는다. 그때 "물리 아님" 은
    커널이 틀렸다는 뜻이 아니라 말할 힘이 없다는 뜻이고, 둘은 다음 행동이
    정반대다 - 앞은 모델을, 뒤는 표본을 고친다."""
    rng = np.random.default_rng(3)
    dg = rng.normal(size=(40, S.N_ZONES)) * 10
    dw = rng.normal(size=(40, S.N_ZONES)) * 0.05      # 완전히 독립
    text = diagnose._sweep_verdict(diagnose.sweep_kernels(dg, dw, alpha=1.0))
    assert "판정할 힘이 없다" in text
    assert "조정 폭" in text


def test_underpowered_rows_are_marked_undecidable_in_the_table():
    """표에서도 '물리 아님' 과 '판정 불가' 가 갈려야 한다."""
    rng = np.random.default_rng(8)
    dg = rng.normal(size=(40, S.N_ZONES)) * 10
    dw = rng.normal(size=(40, S.N_ZONES)) * 0.05
    tables = {
        "07_delta_samples": _samples(dg, dw),
        "05_aligned": pd.DataFrame(), "06_response_curve": pd.DataFrame(),
    }
    assert "판정 불가" in diagnose.render(tables, alpha=1.0)


def test_resolvable_kernel_is_not_called_undecidable():
    """신호가 충분하면 게이트가 열려야 한다 - 안 그러면 아무것도 판정 못 한다."""
    dg, dw = _wide_kernel_samples()
    rows = diagnose.sweep_kernels(dg, dw, alpha=1.0)
    assert any(r["resolvable"] for r in rows)
    assert "COATING_KERNEL_HALF_WIDTH" in diagnose._sweep_verdict(rows)


def test_scale_section_refuses_to_compare_noise_with_noise():
    """final 이 2σ 를 못 넘으면 기대 탭을 만들지 않는다. 실측에서 노이즈끼리
    견주어 '크기는 맞다' 가 나왔던 자리다."""
    dg, dw = _wide_kernel_samples()
    curve = _curve(final=0.0018)
    curve["sem"] = 0.0020                              # final < 2·SEM
    tables = {
        "07_delta_samples": _samples(dg, dw),
        "05_aligned": _aligned(d_gap=1.0), "06_response_curve": curve,
    }
    text = diagnose.render(tables, alpha=1.0)
    assert "대조 불가" in text
    assert "크기는 맞다" not in text


def test_scale_section_flags_sign_disagreement():
    """동특성과 커널이 반대 방향을 가리키면 크기 비교보다 그것이 먼저다."""
    dg, dw = _wide_kernel_samples()                    # 커널 중심은 양수
    tables = {
        "07_delta_samples": _samples(dg, dw),
        "05_aligned": _aligned(d_gap=1.0),
        "06_response_curve": _curve(final=-0.5),       # 동특성은 음수
    }
    assert "부호가 반대다" in diagnose.render(tables, alpha=1.0)


def test_null_space_finds_conserving_group():
    """세 zone 의 합을 늘 0 으로 유지하면 그 조합이 영공간에 남는다."""
    rng = np.random.default_rng(4)
    dg = np.zeros((40, S.N_ZONES))
    for z in range(1, 24):
        dg[:, z] = rng.normal(size=40)
    dg[:, 6] = -(dg[:, 4] + dg[:, 5])                 # zone 5·6·7 총량 보존
    rel = diagnose.null_space_zones(dg)
    assert len(rel) == 1
    zones = {m["zone"] for m in rel[0]["members"]}
    assert zones == {5, 6, 7}


def test_null_space_ignores_never_adjusted_zones():
    """안 움직인 zone 은 이미 설명된 결손이다. 목록에 끼면 진짜를 덮는다."""
    rng = np.random.default_rng(5)
    dg = np.zeros((40, S.N_ZONES))
    for z in range(1, 24):                            # zone 1·25 는 그대로 0
        dg[:, z] = rng.normal(size=40)
    assert diagnose.null_space_zones(dg) == []


def test_null_space_marks_trade_off_as_mixed_sign():
    """한쪽을 열고 다른 쪽을 닫는 맞교환은 부호가 섞인다 — 총량 보존과 다르다."""
    rng = np.random.default_rng(6)
    dg = np.zeros((40, S.N_ZONES))
    for z in range(1, 24):
        dg[:, z] = rng.normal(size=40)
    dg[:, 6] = -dg[:, 5]
    rel = diagnose.null_space_zones(dg)
    assert rel and rel[0]["conserving"] is False


def test_render_produces_all_three_sections():
    dg, dw = _wide_kernel_samples()
    tables = {
        "07_delta_samples": _samples(dg, dw),
        "05_aligned": _aligned(d_gap=12.0),
        "06_response_curve": _curve(final=0.4),
    }
    text = diagnose.render(tables, alpha=1.0)
    assert "## 1. 크기" in text
    assert "## 2. k 스윕" in text
    assert "## 3. 랭크 결손" in text


def test_render_survives_missing_aligned_tables():
    """05·06 이 없어도 2·3 절은 나와야 한다. 한 절이 없다고 도구가 죽으면
    부분적으로라도 답할 수 있는 질문까지 못 답한다."""
    dg, dw = _wide_kernel_samples()
    tables = {
        "07_delta_samples": _samples(dg, dw),
        "05_aligned": pd.DataFrame(),
        "06_response_curve": pd.DataFrame(),
    }
    text = diagnose.render(tables, alpha=1.0)
    assert "대조할 수 없다" in text
    assert "## 2. k 스윕" in text


def test_scale_verdict_splits_three_ways():
    """크기가 맞다 / 부족하다 / 자릿수로 다르다 — 다음 행동이 전부 다르다."""
    assert "크기는 맞다" in diagnose._scale_verdict(0.8)
    assert "크기가 부족하다" in diagnose._scale_verdict(0.1)
    assert "자릿수로 다르다" in diagnose._scale_verdict(0.01)


def test_load_dump_explains_missing_folder_in_a_sentence(tmp_path):
    """트레이스백을 던지면 사용자가 돌려줄 수 있는 것이 스택뿐이다."""
    with pytest.raises(SystemExit) as e:
        diagnose.load_dump(tmp_path / "없는폴더")
    assert "덤프 폴더가 없다" in str(e.value)
    assert "--dump" in str(e.value)


def test_load_dump_points_at_stale_code_when_table_is_absent(tmp_path):
    """07 이 없는 덤프는 예전 코드가 만든 것이다. 그 사실을 알려줘야 한다."""
    (tmp_path / "05_aligned.csv").write_text("a\n", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        diagnose.load_dump(tmp_path)
    assert "07_delta_samples" in str(e.value)
    assert "git pull" in str(e.value)


# ── 밀림 탐지 ───────────────────────────────────────────────────────
# 노이즈와 밀림은 둘 다 "종 모양이 아니다" 로 나오지만 고칠 곳이 정반대다.
# 노이즈는 데이터를 더 모아야 하고, 밀림은 설비 도면을 봐야 한다.


def _shifted_samples(shift: int, seed=11, gain=0.044):
    """Wet zone i 가 실제로는 gap zone i-shift 에 반응하는 데이터.

    부호 규약: roll(+s) 로 심으면 원래 dg 로 적합했을 때 봉우리가 offset -s 에
    선다. 커널 offset 은 "측정 zone 기준 몇 칸 옆의 gap 을 보는가" 이므로,
    gap 을 오른쪽으로 민 것은 왼쪽 gap 을 보는 것과 같기 때문이다.
    """
    rng = np.random.default_rng(seed)
    true = np.array([0.006, 0.022, gain, 0.022, 0.006])
    dg = np.zeros((60, S.N_ZONES))
    for e in range(60):
        for z in rng.choice(np.arange(1, 24), size=3, replace=False):
            dg[e, z] = rng.choice([-1, 1]) * rng.uniform(5, 20)
    dw = (build_design(np.roll(dg, shift, axis=1), 2) @ true).reshape(60, S.N_ZONES)
    dw += rng.normal(0, 0.3, (60, 1)) + rng.normal(0, 0.02, (60, S.N_ZONES))
    dw[:, [0, 17, 24]] = np.nan
    return dg, dw


def test_sweep_reports_where_the_peak_stands():
    """봉우리 위치가 나와야 밀림을 볼 수 있다."""
    dg, dw = _wide_kernel_samples()
    rows = diagnose.sweep_kernels(dg, dw, alpha=1.0)
    assert all("peak_offset" in r for r in rows)
    assert {r["k"]: r["peak_offset"] for r in rows}[5] == 0


def test_peak_consensus_detects_a_one_zone_shift():
    """넓은 k 가 전부 같은 자리를 가리키면 밀림이다."""
    shift = 2
    dg, dw = _shifted_samples(shift=shift)
    rows = diagnose.sweep_kernels(dg, dw, alpha=1.0)
    assert diagnose.peak_offset_consensus(rows) == -shift


def test_peak_consensus_is_silent_when_aligned():
    """정렬이 맞으면 봉우리가 0 에 서므로 밀림이라고 말하면 안 된다."""
    dg, dw = _wide_kernel_samples()
    assert diagnose.peak_offset_consensus(diagnose.sweep_kernels(dg, dw, 1.0)) is None


def test_peak_consensus_is_silent_on_pure_noise():
    """노이즈는 k 마다 다른 자리를 가리킨다. 그것을 밀림이라 부르면
    사용자를 설비 도면으로 보내는 헛걸음을 시킨다."""
    rng = np.random.default_rng(9)
    dg = rng.normal(size=(40, S.N_ZONES)) * 10
    dw = rng.normal(size=(40, S.N_ZONES)) * 0.05
    assert diagnose.peak_offset_consensus(diagnose.sweep_kernels(dg, dw, 1.0)) is None


def test_sweep_verdict_sends_shift_to_the_equipment_not_the_data():
    """밀림 판정은 데이터를 더 모으라고 하면 안 된다 — 설비를 확인해야 한다."""
    dg, dw = _shifted_samples(shift=2)
    text = diagnose._sweep_verdict(diagnose.sweep_kernels(dg, dw, alpha=1.0))
    assert "밀렸다" in text
    assert "설비" in text
    assert "COATING_RIDGE_ALPHA" not in text


# ── 격리 표 CLI ─────────────────────────────────────────────────────


def test_isolation_verdict_starts_work_when_events_survive():
    t = pd.DataFrame([{"post_minutes": 60, "pre_minutes": 30,
                       "n_events": 9, "n_isolated": 3, "ratio": 1 / 3}])
    text = diagnose._isolation_verdict(t, n_current=3)
    assert "3건으로 시작할 수 있다" in text
    assert "L+T_s" in text


def test_isolation_verdict_points_at_a_narrower_window():
    """현재 창에서 0건이어도 살아나는 창이 있으면 그 값을 짚어 준다."""
    t = pd.DataFrame([
        {"post_minutes": 10, "pre_minutes": 5, "n_events": 9,
         "n_isolated": 4, "ratio": 0.44},
        {"post_minutes": 60, "pre_minutes": 30, "n_events": 9,
         "n_isolated": 0, "ratio": 0.0},
    ])
    text = diagnose._isolation_verdict(t, n_current=0)
    assert "COATING_ISOLATION_POST_MINUTES" in text
    assert "10" in text


def test_isolation_verdict_asks_for_data_when_nothing_ever_survives():
    """어느 창에서도 0 이면 설정으로는 못 푼다 - 데이터를 요청해야 한다."""
    t = pd.DataFrame([{"post_minutes": w, "pre_minutes": w // 2,
                       "n_events": 9, "n_isolated": 0, "ratio": 0.0}
                      for w in (10, 30, 60)])
    text = diagnose._isolation_verdict(t, n_current=0)
    assert "설정으로는 못 푼다" in text
    assert "요청한다" in text


def test_cli_requires_choosing_what_to_diagnose():
    """둘 다 안 주면 무엇을 볼지 모른다. 기본값을 정하면 원본과 파생물 중
    엉뚱한 쪽을 조용히 본다."""
    with pytest.raises(SystemExit) as e:
        diagnose.main([])
    assert "--isolation" in str(e.value) and "--dump" in str(e.value)
