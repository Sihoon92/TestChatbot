"""샘플 데이터가 스키마를 만족하고, 화면의 모든 상태를 덮는지 확인한다.

샘플이 한 가지 경우(정상 금형)만 담고 있으면 빈 상태·에러 표시 UI 를 손으로
확인할 방법이 없다. 대시보드가 그려야 하는 상태를 샘플이 실제로 포함하는지
여기서 강제한다.
"""
from app.molds.sample_data import SAMPLE_MOLDS
from app.molds.schemas import MoldDetail


def test_all_samples_are_valid_details():
    assert len(SAMPLE_MOLDS) >= 4
    for mold in SAMPLE_MOLDS:
        assert isinstance(mold, MoldDetail)


def test_mold_numbers_are_unique():
    numbers = [m.summary.mold_no for m in SAMPLE_MOLDS]
    assert len(numbers) == len(set(numbers))


def test_samples_cover_every_status():
    statuses = {m.summary.status for m in SAMPLE_MOLDS}
    assert statuses >= {"in_use", "standby", "repair"}


def test_non_in_use_molds_have_no_installation():
    """대기중·수리중 금형은 라인/호기가 없어야 한다(종속 필터의 전제)."""
    for mold in SAMPLE_MOLDS:
        if mold.summary.status != "in_use":
            assert mold.summary.line is None
            assert mold.summary.machine is None


def test_in_use_molds_have_installation():
    for mold in SAMPLE_MOLDS:
        if mold.summary.status == "in_use":
            assert mold.summary.line is not None
            assert mold.summary.machine is not None


def test_samples_cover_missing_and_error_stages():
    """탭 배지 UI 를 확인하려면 두 상태가 샘플에 있어야 한다."""
    all_statuses = {
        status
        for mold in SAMPLE_MOLDS
        for status in mold.summary.stage_status.values()
    }
    assert "missing" in all_statuses
    assert "error" in all_statuses


def test_some_design_fields_are_null():
    """`—` 렌더를 확인하려면 null 인 설계 필드가 있어야 한다."""
    has_null = any(
        mold.design.angle_deg is None or mold.design.height_mm is None
        for mold in SAMPLE_MOLDS
    )
    assert has_null


def test_stages_contain_only_flexible_stages():
    """stages 배열에는 유연 단계 3개만 들어간다(design/install 은 전용 필드가 있다)."""
    for mold in SAMPLE_MOLDS:
        keys = {panel.stage for panel in mold.stages}
        assert keys <= {"iqc", "pqc", "ai_recheck"}


def test_stage_status_covers_all_five_stages():
    for mold in SAMPLE_MOLDS:
        assert set(mold.summary.stage_status) == {
            "design", "iqc", "pqc", "install", "ai_recheck"
        }


def test_sample_includes_mold_with_unknown_quantities():
    """수량 미상 금형이 샘플에 있어야 화면의 `—` 렌더를 손으로 확인할 수 있다.
    0(신품)과 None(미상)은 다른 상태이고, 화면이 이를 구분해야 한다."""
    unknown = [
        m for m in SAMPLE_MOLDS
        if m.summary.shot_count is None and m.history.total_installs is None
    ]
    assert unknown, "수량이 전부 None 인 금형 샘플이 필요하다"


def test_sample_mold_numbers_use_real_format():
    """실물 금형번호는 'RX28312' 형태다. 'M-1024' 는 초기 추측이었다."""
    for m in SAMPLE_MOLDS:
        assert m.summary.mold_no.startswith("RX"), m.summary.mold_no
