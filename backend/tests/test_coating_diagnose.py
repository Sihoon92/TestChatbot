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


# ── 병합창 진단 ─────────────────────────────────────────────────────


def _hist(counts):
    from app.coating.events import _GAP_LABELS
    total = sum(counts) or 1
    return pd.DataFrame({"bucket": _GAP_LABELS, "n": counts,
                         "ratio": [c / total for c in counts]})


def test_gap_verdict_reports_the_whole_valley_not_just_its_first_cell():
    """골이 여러 칸에 걸치면 그 폭이 곧 '이 사이 아무 값으로나' 라는 여유다."""
    #        0~1 1~2 2~3 3~5 5~10 10~20 20~60 60+
    text = diagnose._gap_verdict(_hist([0, 0, 3, 0, 0, 4, 0, 2]))
    assert "3~10분" in text


def test_gap_verdict_ignores_empty_cells_at_the_edges():
    """앞뒤 끝의 빈 칸은 골이 아니라 그냥 범위 밖이다."""
    text = diagnose._gap_verdict(_hist([0, 0, 5, 4, 3, 0, 0, 0]))
    assert "골이 없다" in text


def test_gap_verdict_says_so_when_gaps_are_continuous():
    """간격이 연속적으로 퍼져 있으면 간격만으로는 못 가른다.

    가리키는 표 번호는 --preprocess 렌더러의 실제 절 번호(병합 민감도 = §7)와
    맞아야 한다 - 안 맞으면 사용자를 없는 절로 보내는 안내문이 된다."""
    text = diagnose._gap_verdict(_hist([1, 2, 3, 4, 3, 2, 1, 1]))
    assert "골이 없다" in text
    assert "7번 표" in text


def test_merge_verdict_quantifies_what_a_narrow_window_costs():
    """'넓히면 좋다' 로는 부족하다 - 몇 개에서 몇 개로 느는지가 있어야 한다."""
    sens = pd.DataFrame([
        {"merge_minutes": 2, "n_clusters": 35, "n_isolated": 3, "n_items": 4},
        {"merge_minutes": 10, "n_clusters": 12, "n_isolated": 9, "n_items": 40},
    ])
    text = diagnose._merge_verdict(sens, current=2)
    assert "4개" in text and "40개" in text
    assert "COATING_EVENT_MERGE_MINUTES" in text
    assert "우리가 버리고 있다" in text


def test_merge_verdict_confirms_when_current_is_already_best():
    sens = pd.DataFrame([
        {"merge_minutes": 2, "n_clusters": 35, "n_isolated": 9, "n_items": 40},
        {"merge_minutes": 10, "n_clusters": 12, "n_isolated": 5, "n_items": 20},
    ])
    assert "현재 2분이 최선" in diagnose._merge_verdict(sens, current=2)


# ── --preprocess 렌더러 ─────────────────────────────────────────────


def test_preprocess_renders_all_five_sections(tmp_path, monkeypatch):
    """다섯 절이 다 있어야 한다. 절이 통째로 사라지면 '안 쟀다' 와
    '재서 0 이다' 를 구별할 수 없다."""
    import pandas as pd

    from app.coating import diagnose, parse
    from app.coating import schemas as S
    from app.config import get_settings

    base = pd.Timestamp("2026-02-02 06:00")
    rows = []
    for m in range(0, 240):
        at = base + pd.Timedelta(minutes=m)
        for zi in range(3):
            v = 41.0 if (zi == 0 and m >= 30) else 40.0
            rows.append({S.LOT: "L1", S.AT: at, S.PRODUCT: "P",
                         S.ITEM: S.GAP_ITEM_IDS[zi], S.VALUE: v,
                         S.ROW_NO: len(rows)})
        for zi in range(3):
            rows.append({S.LOT: "L1", S.AT: at, S.PRODUCT: "P",
                         S.ITEM: S.WET_ITEM_IDS[zi],
                         S.VALUE: 18.0 if m < 38 else 18.4,
                         S.ROW_NO: len(rows)})
    src = tmp_path / "synth.parquet"
    pd.DataFrame(rows).to_parquet(src)

    monkeypatch.setattr(
        parse, "load_readings",
        lambda *a, **k: pd.read_parquet(src).assign(**{
            S.IO: lambda d: [
                S.IO_OUTPUT if i.startswith("9") else S.IO_INPUT for i in d[S.ITEM]
            ]
        }),
    )
    text = diagnose.render_preprocess(str(src), get_settings())
    for head in ("## 0.", "## 1.", "## 2.", "## 3.", "## 4."):
        assert head in text

    # 제목만 있고 본문이 깨져도(또는 비어도) 위 for 문은 통과한다 - 절마다
    # 손으로 미리 계산한 값을 하나씩 박는다. fixture 는 zone1 gap 이 m=30 에
    # 40→41 로, Wet zone 1~3 이 m=38 에 18.0→18.4 로 한 번씩만 계단을 밟는
    # 단일 이벤트다. 손 계산:
    #   원본 행 = 240분 × (gap 3종 + wet 3종) = 1,440. dedupe 는 중복이 없어
    #   그대로 1,440. compress_runs 는 항목당 시작값 1행 + 실제 변경(zone1 gap
    #   1회, wet 3종 각 1회) = 6 + 4 = 10행. 그중 제어 항목(gap)만 4행(시작값
    #   3 + 변경 1), prev_value 가 있는 것(진짜 변경)은 1행뿐이다. 이벤트는
    #   1건, 격리도 1건(이웃도 없고 lot 경계까지 앞30분/뒤209분 여유) → §0 의
    #   "격리 통과" 줄은 n=1, 탈락 사유는 전부 0.
    assert "원본 행                      1,440" in text
    assert "값이 바뀐 시점만                    10  (-1,430)" in text
    assert "격리 통과                         1   탈락 앞 0 · 뒤 0 · 양쪽 0" in text

    #   §1 원장: 이벤트 id 는 "{lot}#{앵커 그룹 번호}" 이므로 "L1#1". 이벤트
    #   시작 m=30, lot 시작 m=0 → gap_before=30.0. lot 끝 m=239, 이벤트
    #   끝(span 0이라 시작과 동일) m=30 → gap_after=209.0. 둘 다 pre/post=10
    #   을 넉넉히 넘어 격리 통과(✓).
    assert "L1#1" in text
    assert "30.0  209.0  ✓" in text

    #   §2 타임라인: lot span = 239분, width 기본 80 → mpc = 239/80 = 2.9875
    #   → 반올림 표시 "3분". lot 끝 시각 = 06:00 + 239분 = 09:59.
    assert "1칸 = 3분" in text
    assert "09:59" in text

    #   §3 구/신 대조: 이벤트가 하나뿐이고 구 규칙(앞30/뒤60)도 여유가
    #   충분해(30≥30, 209≥60) 신·구 모두 그 하나를 살린다 → 양쪽 통과 1건,
    #   신에만/구에만 0건.
    assert "양쪽 통과 1건 · 신에만 0건 · 구에만 0건" in text

    #   §4 응답창: t0=m30, baseline=[27,30) 은 Wet 이 아직 18.0 이라 기준선
    #   18.0. Wet 은 m=38 에 18.4 로 계단(step) → lag=38-30=8 부터 반응이
    #   보이고(lag 8·9·10 모두 0.4, 그 앞은 0), 창은 post=10 분까지라
    #   last_lag=10. tie 는 앞선 lag(8)를 고른다 → 최댓값 lag=+8, 관측
    #   끝=+10, 마지막 칸이 아니므로 경고 없음(✓).
    assert "lag = +8분 (관측 끝 +10분)" in text


def test_preprocess_survives_a_response_that_is_all_nan(tmp_path, monkeypatch):
    """§4 가 예외 없이, 트레이스백 없이 §0~§3 을 살려낸다(전체 fix A 회귀).

    조정한 zone(zone5)의 Wet 이 그 lot 에서 통째로 0(=미측정)이면, 그 이벤트의
    응답은 모든 lag 에서 NaN 이다 - align_events 의 유일한 가드
    (`base.isna().all()`)는 zone1 처럼 **다른** zone 에 Wet 값이 있으면
    통과하므로 이 케이스를 못 거른다. §4 의 옛 코드는 `fwd["mean"].idxmax()`
    를 all-NaN Series 에 그대로 불러 pandas 3.0.5 에서
    `ValueError: Encountered all NA values` 를 던졌고, render_preprocess 가
    lines 를 끝에 한 번만 join 하므로 그 예외가 이미 계산된 §0~§3 까지
    통째로 삼켰다.
    """
    import pandas as pd

    from app.coating import diagnose, parse
    from app.coating import schemas as S
    from app.config import get_settings

    base = pd.Timestamp("2026-02-02 06:00")
    rows = []
    for m in range(0, 200):
        at = base + pd.Timedelta(minutes=m)
        # zone1 gap 은 안 바뀐다 - 격리·이벤트 계산에 끼어들지 않게.
        rows.append({S.LOT: "L1", S.AT: at, S.PRODUCT: "P",
                     S.ITEM: S.GAP_ITEM_IDS[0], S.VALUE: 40.0, S.ROW_NO: len(rows)})
        # zone5 gap 은 m=30 에 한 번 조정된다 - 이 이벤트의 응답을 §4 가 본다.
        rows.append({S.LOT: "L1", S.AT: at, S.PRODUCT: "P",
                     S.ITEM: S.GAP_ITEM_IDS[4],
                     S.VALUE: 41.0 if m >= 30 else 40.0, S.ROW_NO: len(rows)})
        # zone1 Wet 은 정상 관측(18.0 고정) - base.isna().all() 가드를 지나가게 한다.
        rows.append({S.LOT: "L1", S.AT: at, S.PRODUCT: "P",
                     S.ITEM: S.WET_ITEM_IDS[0], S.VALUE: 18.0, S.ROW_NO: len(rows)})
        # zone5 Wet 은 이 lot 에서 통째로 미측정(0) - wet_wide 가 NaN 으로 마스킹한다.
        rows.append({S.LOT: "L1", S.AT: at, S.PRODUCT: "P",
                     S.ITEM: S.WET_ITEM_IDS[4], S.VALUE: 0.0, S.ROW_NO: len(rows)})
    src = tmp_path / "synth_all_nan.parquet"
    pd.DataFrame(rows).to_parquet(src)

    monkeypatch.setattr(
        parse, "load_readings",
        lambda *a, **k: pd.read_parquet(src).assign(**{
            S.IO: lambda d: [
                S.IO_OUTPUT if i.startswith("9") else S.IO_INPUT for i in d[S.ITEM]
            ]
        }),
    )
    text = diagnose.render_preprocess(str(src), get_settings())

    for head in ("## 0.", "## 1.", "## 2.", "## 3.", "## 4."):
        assert head in text
    assert "Traceback" not in text
    assert "ValueError" not in text
    assert "정렬된 응답이 없다" in text


def test_isolation_flag_is_an_alias_for_preprocess():
    from app.coating import diagnose

    p = diagnose.build_parser()
    args = p.parse_args(["--isolation", "x.parquet"])
    assert args.preprocess_input == "x.parquet"
