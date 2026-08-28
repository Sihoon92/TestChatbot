"""컬럼 계약 — 여기가 흔들리면 파이프라인 전 단계의 조인이 조용히 깨진다."""
from app.coating import schemas as S
from app.config import get_settings


def test_zone_col_is_stable_and_one_based():
    """zone 은 1..25 다. 0-based 로 바뀌면 25번 zone 이 사라진다."""
    assert S.zone_col(1) == "z1"
    assert S.zone_col(25) == "z25"
    assert len(S.ZONE_COLS) == 25
    assert S.ZONE_COLS[0] == "z1"


def test_gap_and_wet_item_ids_cover_25_zones_each():
    """T_Block 30030838~30030862, GV Wet 90030611~90030635 각각 25개.
    하나라도 빠지면 영향행렬의 행/열이 어긋난다."""
    assert len(S.GAP_ITEM_IDS) == 25
    assert len(S.WET_ITEM_IDS) == 25
    assert S.GAP_ITEM_IDS[0] == "30030838"
    assert S.GAP_ITEM_IDS[24] == "30030862"
    assert S.WET_ITEM_IDS[0] == "90030611"
    assert S.WET_ITEM_IDS[24] == "90030635"


def test_control_scalars_map_item_id_to_feature_name():
    """레벨 모델의 피처 4개. item_id 를 그대로 컬럼명으로 쓰면 모델 계수를
    사람이 읽을 수 없다."""
    assert S.CONTROL_SCALARS["10030009"] == "bp_open_rate"
    assert S.CONTROL_SCALARS["50030111"] == "pump_rpm"
    assert S.CONTROL_SCALARS["10030271"] == "os_gap"
    assert S.CONTROL_SCALARS["10030272"] == "ds_gap"


def test_coating_settings_have_defaults_and_resolve_path():
    """설정이 .env 단일 출처인지. 상대경로는 backend/ 기준으로 풀려야 한다."""
    s = get_settings()
    assert s.coating_event_merge_minutes >= 1
    assert s.coating_kernel_half_width >= 1
    assert s.coating_settle_std_max > 0
    assert s.resolved_coating_data_dir.endswith("coating")


def test_csv_encoding_candidates_come_from_settings():
    """실데이터 인코딩은 사업부·설비마다 다르다. 코드에 박으면 사내 PC 에서
    한 줄 고치자고 배포를 다시 해야 한다 — .env 단일 출처 규칙."""
    s = get_settings()
    assert s.coating_csv_encoding_list[0] == "utf-8-sig"
    assert "cp949" in s.coating_csv_encoding_list


def test_input_source_is_switchable_by_setting():
    """DRM 때문에 CSV 를 못 읽는 동안 xlsx 로 우회한다. 어느 쪽을 읽을지는
    코드가 아니라 .env 가 정한다 — 사내 PC 에서 두 줄만 바꿔 전환한다."""
    s = get_settings()
    assert s.coating_input_format in ("csv", "xlsx")
    assert s.resolved_coating_input_path.endswith(".csv")
    assert s.coating_xlsx_sheet == ""


def test_input_path_resolves_under_coating_data_dir():
    """상대경로는 backend/ 가 아니라 COATING_DATA_DIR 기준이다. raw/ 아래에
    원본을 두는 규약을 설정 한 줄로 유지한다."""
    s = get_settings()
    assert s.resolved_coating_input_path.startswith(s.resolved_coating_data_dir)
