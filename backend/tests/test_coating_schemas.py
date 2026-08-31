"""컬럼 계약 — 여기가 흔들리면 파이프라인 전 단계의 조인이 조용히 깨진다."""
import pytest

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
    """DRM 때문에 CSV 를 못 읽는 동안 xlsx 로 우회하고, 한 번 변환한 뒤에는
    parquet 으로 간다. 어느 쪽을 읽을지는 코드가 아니라 .env 가 정한다 —
    사내 PC 에서 두 줄만 바꿔 전환한다.

    개발자의 로컬 .env 에 실제로 적힌 값을 단언하지 않는다. .env 는 gitignore
    대상이라 사람마다 다르고, 실제로 여기서 parquet 으로 바꾸자 예전 단언
    (endswith('.csv'))이 깨졌다. 검증할 것은 "설정이 결과를 바꾼다" 이지
    "지금 설정이 무엇이다" 가 아니다.
    """
    s = get_settings()
    assert s.coating_input_format in ("csv", "xlsx", "parquet")
    for fmt, name in (("csv", "a.csv"), ("xlsx", "b.xlsx"), ("parquet", "c.parquet")):
        moved = s.model_copy(update={"coating_input_format": fmt,
                                     "coating_input_path": f"raw/{name}"})
        assert moved.coating_input_format == fmt
        assert moved.resolved_coating_input_path.endswith(name)


def test_input_path_resolves_under_coating_data_dir():
    """상대경로는 backend/ 가 아니라 COATING_DATA_DIR 기준이다. raw/ 아래에
    원본을 두는 규약을 설정 한 줄로 유지한다."""
    s = get_settings()
    assert s.resolved_coating_input_path.startswith(s.resolved_coating_data_dir)


# ── 헤더 정규화 ─────────────────────────────────────────────────────────
#
# 아래는 전부 파일도 Excel 도 없이 도는 순수 테스트다 — schemas 는 app 안의
# 어떤 모듈도 import 하지 않으므로 계약 전체를 여기서 검증할 수 있다.


@pytest.mark.parametrize(
    "header",
    ["worked_at", "WorkedAt", "worked at", "WORKED-AT", "  worked_at  ", "Worked.At"],
)
def test_english_spelling_variants_collapse_to_one_key(header):
    """표기 흔들림만 없앤다. 영어 변형 지원은 별칭 표가 아니라 정규화에서 나온다."""
    assert S.normalize_header(header) == S.normalize_header("worked_at")


def test_fullwidth_characters_are_folded():
    """중국어·일본어 IME 는 전각을 만든다. NFKC 가 접지 않으면 눈으로는 같은
    글자인데 매칭이 안 되는, 가장 설명하기 어려운 실패가 된다."""
    assert S.normalize_header("ｖａｌｕｅ") == "value"


def test_invisible_characters_are_removed():
    """utf-8-sig 는 파일 앞 BOM 만 뗀다. xlsx(COM) 경로와 이중 BOM 파일에서는
    헤더 문자열 안에 남아 보이지 않는 불일치를 만든다."""
    assert S.normalize_header("﻿lot_id") == S.normalize_header("lot_id")


def test_trailing_parenthetical_is_dropped():
    """MES 는 단위·비고를 헤더 끝 괄호에 적는다. 이걸 못 떼면 사람이 원본을
    열어 괄호를 지우는 왕복이 생긴다."""
    assert S.normalize_header("数值(mg/cm2)") == S.normalize_header("数值")
    assert S.normalize_header("측정값 (참고)") == S.normalize_header("측정값")


def test_header_made_only_of_parentheses_is_not_erased():
    """괄호가 전부인 헤더까지 지우면 빈 문자열이 된다. 그건 정규화가 아니라
    파괴이고, 빈 키는 아무 컬럼에나 걸릴 수 있다."""
    assert S.normalize_header("(값)") != ""


# ── 별칭 표 ─────────────────────────────────────────────────────────────


def test_alias_table_has_no_cross_column_collision():
    """import 시점 검사의 명시적 회귀. 표가 100개 가까이 되므로 '品名' 을
    product 와 item_name 양쪽에 적는 실수를 사람이 읽어서 잡을 수 없다.
    조용히 덮어쓰면 그 열이 통째로 엉뚱한 자리에 들어간다."""
    owner: dict[str, str] = {}
    for canonical, by_lang in S.COLUMN_ALIASES.items():
        for aliases in by_lang.values():
            for alias in aliases:
                key = S.normalize_header(alias)
                assert key, f"빈 별칭: {alias!r}"
                assert owner.get(key, canonical) == canonical, (
                    f"{alias!r} 가 {owner[key]} 와 {canonical} 양쪽에 있다"
                )
                owner[key] = canonical


def test_every_aliasable_column_is_recognized_by_its_own_name():
    """표준 이름은 별칭 표에 적지 않는다. 역인덱스가 먼저 넣어주는지 확인한다."""
    for canonical in S.ALIASABLE_COLUMNS:
        assert S._ALIAS_TO_CANONICAL[S.normalize_header(canonical)] == canonical


@pytest.mark.parametrize(
    "header,expected",
    [
        ("批次号", S.LOT),          # 간체
        ("批次號", S.LOT),          # 번체
        ("作业时间", S.AT),
        ("作業時間", S.AT),
        ("产品", S.PRODUCT),
        ("產品", S.PRODUCT),
        ("项目编号", S.ITEM),
        ("項目編號", S.ITEM),
        ("数值", S.VALUE),
        ("數值", S.VALUE),
        ("작업일시", S.AT),
        ("측정값", S.VALUE),
        # 실제 사내 MES 헤더. 띄어쓰기는 정규화가 흡수하므로 원본 표기 그대로
        # 표에 적고, "제품약칭"·"프로젝트코드" 처럼 붙여 쓴 것도 함께 걸린다.
        ("작업일", S.AT),
        ("제품 약칭", S.PRODUCT),
        ("제품약칭", S.PRODUCT),
        ("프로젝트 코드", S.ITEM),
        ("프로젝트코드", S.ITEM),
        ("프로젝트명", S.ITEM_NAME),
    ],
)
def test_simplified_and_traditional_both_map(header, expected):
    """NFKC 는 전각만 접고 간체↔번체는 바꾸지 않는다. 둘 다 표에 있어야 한다."""
    assert S._ALIAS_TO_CANONICAL[S.normalize_header(header)] == expected


@pytest.mark.parametrize("header", ["时间", "시각", "time", "date"])
def test_unqualified_time_words_are_deliberately_not_aliases(header):
    """무자격 일반명사는 일부러 뺐다. 원본에 흔한 무관한 메타 컬럼(更新时间)과
    겹쳐 멀쩡한 파일을 충돌로 죽인다. 자격이 붙은 형태만 받는다."""
    assert S.normalize_header(header) not in S._ALIAS_TO_CANONICAL


# ── require_columns ─────────────────────────────────────────────────────


def test_returns_mapping_from_original_header_to_canonical():
    header = ["批次号", "作业时间", "产品", "项目编号", "数值"]
    assert S.require_columns(header, "x.csv", "") == {
        "批次号": S.LOT,
        "作业时间": S.AT,
        "产品": S.PRODUCT,
        "项目编号": S.ITEM,
        "数值": S.VALUE,
    }


def test_two_headers_for_the_same_column_is_an_error():
    """어느 쪽이 진짜인지 알 방법이 없다. 조용히 하나를 고르면 잘못된 열로
    파이프라인 전체가 돌고 결과는 그럴듯해 보인다.

    이 시끄러운 실패가 있기 때문에 괄호 제거 같은 공격적 정규화를 안전하게
    쓸 수 있다 — 과잉 매칭의 결과가 침묵이 아니라 예외다."""
    header = ["lot_id", "worked_at", "product", "item_id", "value", "数值"]
    with pytest.raises(ValueError) as e:
        S.require_columns(header, "x.csv", "")
    message = str(e.value)
    assert "value" in message and "数值" in message


def test_missing_column_message_shows_what_was_recognized():
    """5개 중 4개가 맞은 상태라면 남은 하나만 고치면 된다는 것을 알아야 한다."""
    header = ["批次号", "时刻", "产品", "项目编号", "数值"]
    with pytest.raises(ValueError) as e:
        S.require_columns(header, "raw/实数据.csv", "  힌트")
    message = str(e.value)
    assert "인식된 헤더" in message
    assert "批次号 -> lot_id" in message
    assert "时刻" in message          # 못 알아본 것도 원문 그대로 보인다


def test_missing_column_message_names_what_to_rename_it_to():
    """이 줄이 왕복 한 번을 없앤다 — 무엇으로 바꾸면 되는지 직접 알려준다."""
    header = ["批次号", "时刻", "产品", "项目编号", "数值"]
    with pytest.raises(ValueError) as e:
        S.require_columns(header, "x.csv", "")
    message = str(e.value)
    assert f"{S.AT} 로 인식하는 이름" in message
    assert "作业时间" in message       # 간체 예시가 언어마다 하나씩 나온다
    assert "작업일시" in message


def test_item_name_is_optional():
    """원본 item_name 은 대부분 비어 있어 사전 것을 쓴다. 없어도 통과해야 한다."""
    header = [S.LOT, S.AT, S.PRODUCT, S.ITEM, S.VALUE]
    assert S.ITEM_NAME not in S.require_columns(header, "x.csv", "").values()


def test_unknown_extra_columns_are_left_alone():
    """모르는 열은 추측하지 않는다. 매핑에 넣지 않고 그대로 흘려보낸다."""
    header = [S.LOT, S.AT, S.PRODUCT, S.ITEM, S.VALUE, "备注", "설비번호"]
    mapping = S.require_columns(header, "x.csv", "")
    assert "备注" not in mapping and "설비번호" not in mapping
