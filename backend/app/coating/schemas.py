"""컬럼 계약과 항목 ID 매핑. 문자열 리터럴은 이 파일에만 존재한다.

다른 모듈이 "lot_id" 를 직접 적기 시작하면 오타가 조용한 조인 실패로 나타난다
(빈 결과가 나오는데 예외는 안 난다). 그래서 상수로 고정한다.

원본 헤더의 다국어 별칭(COLUMN_ALIASES)도 여기 산다. 표준 이름은 영어 하나뿐이고
번역은 parse._finalize 한 곳에서만 일어난다 - 뒤 단계(pivot·events·features·model)는
원본이 무슨 언어였는지 알 필요가 없다. 그게 갈라지는 순간 계약이 언어 수만큼 늘어난다.

app 안의 어떤 모듈도 import 하지 않는다(stdlib 만 쓴다). 이 파일이 계약의 단일
출처인데 다른 모듈을 부르기 시작하면 그 모듈이 다시 이 파일을 필요로 하는 순환이
생긴다 - 실제로 검증을 parse 에 뒀다가 excel_source 와 서로 부르는 구조가 됐었다.
"""
import re
import unicodedata

# 원본 readings
LOT = "lot_id"
AT = "worked_at"
PRODUCT = "product"
ITEM = "item_id"
VALUE = "value"
ROW_NO = "row_no"

# 항목 사전
ITEM_NAME = "item_name"
IO = "io"
ROLE = "role"
ZONE = "zone"

IO_INPUT = "input"
IO_OUTPUT = "output"

# run-length 압축 결과
PREV_VALUE = "prev_value"

# 이벤트
EVENT = "event_id"
DELTA = "delta"
SETTLED_AT = "settled_at"
CONTAMINATED = "contaminated"
DROP_REASON = "drop_reason"

# 파생
WET_MEAN = "wet_mean"
SEGMENT = "segment_id"

# 원본에 반드시 있어야 하는 컬럼. item_name 은 빠져도 된다 - 원본 것은 대부분
# 비어 있고 사전 것을 쓴다.
REQUIRED_COLUMNS = (LOT, AT, PRODUCT, ITEM, VALUE)

# 별칭으로 알아볼 컬럼 전부. item_name 은 필수가 아니지만 여기 있어야 한다 -
# _finalize 가 이 열을 버리는데, 중국어 이름으로 오면 못 알아보고 남겨서
# 같은 데이터의 CSV(영문 헤더)와 결과 DataFrame 이 달라진다.
ALIASABLE_COLUMNS = REQUIRED_COLUMNS + (ITEM_NAME,)

# ── 원본 헤더 별칭 ──────────────────────────────────────────────────────
#
# 사내 MES·해외 법인 export 는 헤더를 자기 언어로 낸다. 지금까지는 사람이 원본을
# 열어 헤더를 영문으로 고쳐 다시 저장했는데, 파일마다 반복되는 수작업인 데다
# DRM 걸린 원본은 그 저장 자체가 어렵다. 읽는 쪽이 알아보는 게 맞다.
#
# app/ingest/normalize.py 의 STATUS_MAP 과 같은 idiom 이다: 어휘 표 하나로 매핑하고
# 못 알아본 것은 추측하지 않고 원문을 드러낸다. 그쪽에서 import 하지는 않는다 -
# 금형 도메인 패키지이고, 이 파일은 아무것도 import 하지 않아야 한다.
#
# 언어별로 묶는 이유는 두 가지다. 사람이 표를 고칠 때 어디에 넣을지 분명해지고,
# 실패 메시지가 언어마다 하나씩 예를 보여줄 수 있다.
#
# 표준 이름 자신(lot_id 등)은 적지 않는다 - 역인덱스를 만들 때 먼저 넣는다.
# NFKC 는 전각만 접고 간체↔번체는 바꾸지 않으므로 둘 다 적어야 한다.
#
# 넣지 않기로 한 것들:
#   - 맨 "时间"·"시각"·"time"·"date" 같은 무자격 일반명사. 무관한 메타 컬럼
#     (更新时间 등)과 겹쳐 충돌 오탐만 만든다.
#   - "型号"·"机种"·"model". 이건 product 의 동의어가 아니라 **다른 개념**이라
#     "产品" 과 한 파일에 같이 있을 수 있다. 그러면 멀쩡한 파일이 충돌로 죽는다.
#     같은 뜻의 다른 표기만 별칭으로 받는다.
COLUMN_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    LOT: {
        "zh-Hans": ("批次号", "批号", "批次"),
        "zh-Hant": ("批次號", "批號"),
        "ko": ("로트번호", "로트", "랏번호", "LOT번호"),
        "en": ("lot", "lotno", "lotnumber", "batch", "batchno", "batchid"),
    },
    AT: {
        "zh-Hans": ("作业时间", "作业时刻", "采集时间", "记录时间", "测量时间"),
        "zh-Hant": ("作業時間", "作業時刻", "採集時間", "記錄時間", "測量時間"),
        "ko": ("작업일시", "작업시각", "작업시간", "측정시각", "측정시간", "수집시각"),
        "en": ("worktime", "datetime", "timestamp", "measuredat", "collectedat"),
    },
    PRODUCT: {
        "zh-Hans": ("产品", "产品编号", "产品代码", "品名"),
        "zh-Hant": ("產品", "產品編號", "產品代碼"),
        "ko": ("제품", "제품코드", "품명", "품번"),
        "en": ("productcode", "productid", "productno"),
    },
    ITEM: {
        "zh-Hans": ("项目编号", "项目代码", "项目号", "点位编号", "参数编号", "测点编号"),
        "zh-Hant": ("項目編號", "項目代碼", "項目號", "點位編號", "參數編號", "測點編號"),
        "ko": ("항목코드", "항목번호", "항목ID", "태그코드", "포인트코드"),
        "en": ("itemcode", "itemno", "tagid", "tagcode", "pointid", "paramid"),
    },
    VALUE: {
        "zh-Hans": ("数值", "测量值", "实测值", "采集值", "值"),
        "zh-Hant": ("數值", "測量值", "實測值", "採集值"),
        "ko": ("측정값", "측정치", "실측값", "값"),
        "en": ("val", "measurement", "measuredvalue", "reading"),
    },
    ITEM_NAME: {
        "zh-Hans": ("项目名称", "项目名", "点位名称", "参数名称"),
        "zh-Hant": ("項目名稱", "項目名", "點位名稱", "參數名稱"),
        "ko": ("항목명", "항목이름", "태그명"),
        "en": ("itemname", "tagname", "pointname", "paramname"),
    },
}

# 헤더 끝에 붙은 괄호 주석 하나. MES 는 단위·비고를 여기 적는다
# ("数值(mg/cm2)", "측정값(참고)"). NFKC 를 먼저 걸어 전각 괄호는 이미 반각이다.
_TRAILING_PAREN = re.compile(r"\([^()]*\)\s*$")

# 표기 흔들림만 만드는 문자들. 이걸 지우면 worked_at·WorkedAt·worked at·
# WORKED-AT 가 전부 같은 키가 된다 - 영어 표기 변형 지원이 여기서 공짜로 나온다.
_NOISE = re.compile(r"[\s_\-./\\]+")

# BOM 과 제로폭 문자. utf-8-sig 가 파일 앞 BOM 은 떼지만, xlsx(COM) 경로와
# 이중 BOM 파일에서는 헤더 문자열 **안에** 남아 눈에 안 보이는 불일치를 만든다.
_INVISIBLE = ("﻿", "​", "‌", "‍", "‎", "‏")


def normalize_header(name: object) -> str:
    """비교용 정규화. 표기 흔들림만 없애고 의미는 추측하지 않는다.

    NFKC 가 전각을 반각으로 접는다(중국어 IME 가 만드는 "ｖａｌｕｅ"·"（"). 다만
    간체↔번체는 바꾸지 않으므로 별칭 표에 둘 다 적어야 한다.
    """
    s = unicodedata.normalize("NFKC", "" if name is None else str(name))
    for ch in _INVISIBLE:
        s = s.replace(ch, "")
    stripped = _TRAILING_PAREN.sub("", s)
    # 괄호가 헤더의 전부인 경우("(값)")까지 지우면 빈 문자열이 된다. 그건
    # 정규화가 아니라 파괴다.
    if stripped.strip():
        s = stripped
    return _NOISE.sub("", s).casefold()


def _build_alias_index() -> dict[str, str]:
    """정규화된 별칭 -> 표준 컬럼명.

    서로 다른 표준명이 같은 정규화 결과를 가지면 import 시점에 죽는다. 표가
    100개 가까이 되므로 "品名" 을 product 와 item_name 양쪽에 적는 실수를 사람이
    읽어서 잡을 수 없다. 조용히 덮어쓰면 그 열이 통째로 엉뚱한 자리에 들어가고,
    결과는 그럴듯해 보인다.
    """
    index: dict[str, str] = {}
    for canonical in ALIASABLE_COLUMNS:
        index[normalize_header(canonical)] = canonical
    for canonical, by_lang in COLUMN_ALIASES.items():
        for aliases in by_lang.values():
            for alias in aliases:
                key = normalize_header(alias)
                if not key:
                    raise AssertionError(f"빈 별칭: {alias!r} ({canonical})")
                owner = index.get(key)
                if owner is not None and owner != canonical:
                    raise AssertionError(
                        f"별칭 충돌: {alias!r} 가 {owner} 와 {canonical} 양쪽에 있다"
                    )
                index[key] = canonical
    return index


_ALIAS_TO_CANONICAL = _build_alias_index()

N_ZONES = 25

# T_Block UNIT Gap Offset 1~25Zone
GAP_ITEM_IDS = [str(30030837 + z) for z in range(1, N_ZONES + 1)]
# (A side)GV UNIT Wet 1~25Zone
WET_ITEM_IDS = [str(90030610 + z) for z in range(1, N_ZONES + 1)]

# zone 이 없는 스칼라 제어값. 레벨 모델의 피처가 된다.
CONTROL_SCALARS = {
    "10030009": "bp_open_rate",
    "50030111": "pump_rpm",
    "10030271": "os_gap",
    "10030272": "ds_gap",
}

# 제어 항목 전체 = 스칼라 4 + zone 25
CONTROL_ITEM_IDS = list(CONTROL_SCALARS) + GAP_ITEM_IDS


# 실패 메시지에 나열할 별칭 예시 개수. 전부 적으면 한 컬럼이 화면을 넘긴다.
_ALIAS_HINT_MAX = 5


def _alias_hint(canonical: str) -> str:
    """그 컬럼으로 인식되는 이름 예시. 언어마다 하나씩 보여준다."""
    by_lang = COLUMN_ALIASES.get(canonical, {})
    shown = [canonical] + [a[0] for a in by_lang.values() if a]
    shown = shown[:_ALIAS_HINT_MAX]
    total = 1 + sum(len(a) for a in by_lang.values())
    text = " / ".join(shown)
    more = total - len(shown)
    return f"{text} … (외 {more}개)" if more > 0 else text


def require_columns(found, path, hint: str) -> dict[str, str]:
    """필수 컬럼 검증. 원본 헤더 -> 표준 컬럼명 매핑을 돌려준다.

    두 입력 경로(csv·xlsx)가 같은 문장으로 실패한다. 검증이 없으면 pandas 가
    뒤에서 KeyError: 'worked_at' 를 던지는데, 그 한 줄로는 헤더를 어떻게 고쳐야
    하는지 알 수 없다.

    헤더는 정규화 후 별칭 표로 찾는다 - 사내 MES·해외 법인 export 는 헤더를 자기
    언어로 내므로, 이름이 다르다고 바로 실패시키면 파일마다 사람이 원본을 고쳐야
    한다. 못 알아본 헤더는 추측하지 않고 메시지에 원문 그대로 드러낸다.
    """
    found = list(found)
    mapping: dict[str, str] = {}
    claimed: dict[str, str] = {}  # 표준명 -> 그 자리를 차지한 원본 헤더
    for col in found:
        canonical = _ALIAS_TO_CANONICAL.get(normalize_header(col))
        if canonical is None:
            continue
        first = claimed.get(canonical)
        if first is not None:
            # 어느 쪽이 진짜인지 알 방법이 없다. 조용히 하나를 고르면 잘못된
            # 열로 파이프라인 전체가 돌고 결과는 그럴듯해 보인다.
            #
            # 이 시끄러운 실패가 있기 때문에 정규화를 공격적으로(괄호 주석 제거
            # 등) 해도 안전하다 - 과잉 매칭의 결과가 침묵이 아니라 예외다.
            raise ValueError(
                f"같은 컬럼을 가리키는 헤더가 둘이다: {first!r} 와 {col!r} "
                f"-> 둘 다 {canonical} ({path})\n"
                f"  실제 헤더: {found}\n"
                "  쓸 열 하나만 남기고 나머지는 이름을 바꾸거나 지운다."
            )
        claimed[canonical] = col
        mapping[col] = canonical

    missing = [c for c in REQUIRED_COLUMNS if c not in claimed]
    if not missing:
        return mapping

    lines = [f"필수 컬럼이 없다: {missing} ({path})", f"  실제 헤더: {found}"]
    if mapping:
        # 무엇이 이미 인식됐는지 보여준다. 5개 중 4개가 맞은 상태라면 남은
        # 하나만 고치면 된다는 것을 여기서 알 수 있다.
        recognized = ", ".join(f"{src} -> {dst}" for src, dst in mapping.items())
        lines.append(f"  인식된 헤더: {recognized}")
    lines.append(f"  필요한 컬럼: {list(REQUIRED_COLUMNS)} (item_name 은 선택)")
    for c in missing:
        # 무엇으로 바꾸면 되는지 직접 알려준다. 이 줄이 왕복 한 번을 없앤다.
        lines.append(f"  {c} 로 인식하는 이름: {_alias_hint(c)}")
    lines.append(hint)
    raise ValueError("\n".join(lines))


def zone_col(z: int) -> str:
    """wide 테이블의 zone 컬럼명. 1-based."""
    return f"z{z}"


ZONE_COLS = [zone_col(z) for z in range(1, N_ZONES + 1)]
