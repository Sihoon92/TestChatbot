"""수집 시험용 샘플 엑셀을 만든다 (EES / JIG기준정보 / MES / IQC / PQC).

실물 파일을 구하기 전에 파이프라인을 끝까지 돌려보기 위한 것이다. 사용자가
설명한 실제 문서 구조를 최대한 그대로 재현했다.

실행 (backend/ 에서, venv 파이썬으로):
    .venv/Scripts/python.exe examples/make_sample_uploads.py

openpyxl 이 없는 환경이라 xlwings(실제 Excel)로 만든다. Excel 이 설치돼 있어야
하고, 만드는 동안 숨김 Excel 프로세스가 잠깐 뜬다.

## 문서 사이의 관계 (이게 이 파일의 핵심이다)

    JIG관리대장 (한 시트에 여러 금형)      JIG기준정보            MES (하루 한 파일)
      JIG ID ────────────────────────→ 금형 확정
      설비명 ───────────→ 기준정보[설비명] ─┐
                            (없으면 폴백)   ├─ 설비코드 ─→ 설비코드
      JIG ID ───────────→ 기준정보[JIG ID] ─┘  + Line명    + 날짜
      위치 "설비" 진입~이탈 = 사용구간 ────────────────────→ 날짜

금형을 확정하는 것은 **관리대장의 JIG ID 열**이다. 이미 알고 있는 값이라 다른
문서를 거칠 필요가 없다. MES 에는 금형번호가 없으므로, 기준정보에서 얻은
설비코드와 사용구간의 날짜로 조회한다.

설비명은 식별이 아니라 **그 구간에 어느 설비의 실적을 붙일지** 고르는 데 쓴다.
금형이 설비를 옮겨 다녀도 구간마다 맞는 실적이 붙고, 설비명이 기준정보에
없어도 금형은 사라지지 않는다(JIG ID 행으로 폴백).

## 일부러 넣어둔 것들 (파이프라인의 방어 장치를 눈으로 확인하려고)

- **표가 A1 에서 시작하지 않는다** (B2 부터). used_range 오프셋 처리를 탄다.
- **JIG ID 에 # 접두사** (#RX41194) → normalize_mold_no 가 떼어 IQC 대장의
  RX41194 와 같은 금형으로 합친다.
- **한 시트에 여러 금형이 시간순으로 섞여 있다** → JIG ID 로 묶지 않으면 A
  금형의 설비 진입이 B 금형의 다음 이벤트로 닫힌다.
- **사용구간의 경계 4종**: 24시간 정확히 걸친 것, 하루 안에 끝난 것, 자정을
  넘겨 다음날까지 가는 것, 아직 설비에 있어 종료가 없는 것.
- **관리대장에 값이 하나도 없는 열**(규격·폐기 기준 등)이 헤더만 있다 →
  find_layout_gaps 가 "값이 있는데 안 잡힌 열"과 구분하는지 확인할 수 있다.
- **MES 2단 병합 헤더** (종합/조립라인/조립별화성 × 4지표).
- **MES 의 TOTAL 행** → 라인이 아니므로 조인에서 빠져야 한다.
- **기준정보에 없는 JIG ID**(#RX77777) → 그 금형이 통째로 빠지는 경로.
- **금형이 다른 설비로 옮겨간 구간**(RX39513 run3) → 구간마다 설비코드가
  달라지는지 확인한다.
- **기준정보에 없는 설비명**(RX50177 run2) → 금형은 남고 JIG ID 행으로 폴백.
- **IQC 에 MES 에 없는 금형(RX99999)** → orphan_mold_nos 에 잡힌다.

## 주의: MES 양식은 확정이 아니다

사용자가 준 MES 컬럼에는 설비코드도, 양품수/불량수도 없었다. 그런데 조회 키가
(날짜, 라인, 설비코드) 이고 집계가 "raw 수량을 합쳐 불량율을 다시 계산"이므로
둘 다 있어야 한다. 없으면 비율의 단순 평균밖에 못 하는데 그건 생산량이 다른
날을 같은 무게로 세는 것이라 틀린 값이다.

그래서 설비코드·투입수량·양품수량·불량수량을 넣어 만들었다. 실물 MES 를 받으면
_MES_HEADER_TOP/_MES_HEADER_BOTTOM 과 make_mes_daily 만 고치면 된다.

불량율(PPM)은 지어낸 숫자가 아니라 불량수량÷투입수량×1,000,000 으로 실제
계산해 넣는다 — 날짜별 raw 를 합쳐 다시 계산한 값과 대조할 수 있어야 한다.
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

import xlwings as xw

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_UPLOADS = _BACKEND_ROOT / "data" / "uploads"

PPM = 1_000_000


# ────────────────────────────────────────────────────────────────────
# 기준 데이터. 세 문서가 이 표를 공유하므로 여기 한 곳에서만 정의한다 —
# 설비명/설비코드/라인이 문서마다 어긋나면 조인이 통째로 안 맞는데,
# 그 어긋남은 "샘플이 잘못된 것"이지 "파이프라인 결함"이 아니라서
# 디버깅이 폭발한다.
# ────────────────────────────────────────────────────────────────────
class Jig:
    def __init__(self, jig_id, jig_name, equip_code, equip_name,
                 line, line_group, shop, process):
        self.jig_id = jig_id            # 'JIG ID' (# 접두사 포함) — 금형 확정 키
        self.jig_name = jig_name        # 'JIG명' (식별에는 안 쓴다)
        self.equip_code = equip_code    # '설비코드' — MES 조회 키
        self.equip_name = equip_name    # '설비명' — 구간별 MES 조회 키를 고른다
        self.line = line                # 'Line명' — MES 조회 키
        self.line_group = line_group    # 'Line Group명' — MES 의 대표라인
        self.shop = shop
        self.process = process


JIGS = [
    Jig("#RX39513", "음극 Notching 금형", 21004780, "POU WND10_Stack(1차)_01",
        "톈진 Pouch #10(S)", "POUCH 조립", "조립", "Pouch Stack"),
    Jig("#RX28312", "양극 Notching 금형", 21004781, "POU WND10_Stack(1차)_02",
        "톈진 Pouch #10(S)", "POUCH 조립", "조립", "Pouch Stack"),
    Jig("#RX28315", "음극 Cutting 금형", 21004793, "POU WND11_Stack(1차)_01",
        "톈진 Pouch #11(S)", "POUCH 조립", "조립", "Pouch Stack"),
    Jig("#RX50177", "상면 Forming 금형", 21005120, "POU WND01_Form(1차)_01",
        "천안 Pouch #1(L)", "POUCH 조립", "조립", "Pouch Forming"),
    Jig("#RX41194", "하면 Forming 금형", 21005121, "POU WND01_Form(1차)_02",
        "천안 Pouch #1(L)", "POUCH 조립", "조립", "Pouch Forming"),
    Jig("#RX39002", "음극 Stack 금형", 21005230, "POU WND02_Stack(1차)_01",
        "천안 Pouch #2(L)", "POUCH 조립", "조립", "Pouch Stack"),
]

BY_ID = {j.jig_id: j for j in JIGS}

# 관리대장에는 이력이 있는데 기준정보에는 없는 금형. 실물에서 가장 흔한 사고가
# "기준정보가 낡아서 새 금형이 빠진" 경우인데, 그러면 MES 조회 키를 못 얻어
# 목록에서 통째로 사라진다. 샘플이 이 경로를 안 타면 unknown_jig_id 카운터가
# 실제로 도는지 확인할 방법이 없다.
UNREGISTERED = Jig(
    "#RX77777", "미등록 Notching 금형", 21009999, "POU WND99_Unknown_01",
    "톈진 Pouch #99(S)", "POUCH 조립", "조립", "Pouch Stack",
)

# 기준정보 어디에도 없는 설비명. 금형은 등록돼 있는데 관리대장에 새 설비(또는
# 오타)가 들어온 경우다 — 금형이 사라지면 안 되고, JIG ID 행의 설비코드로
# 폴백하되 그 사실이 unknown_equipment 로 드러나야 한다.
UNKNOWN_EQUIP = "POU WND98_Typo_01"

# 사용구간이 덮는데 파일이 없는 날. 불량율이 일부 날만 반영됐다는 사실이
# 드러나는지(missing_mes_days) 보려면 하루가 비어 있어야 한다.
# 07-03 은 RX39513(07-01~04)과 RX28312(07-02~03) 두 구간에 걸친다.
MES_MISSING_DAY = date(2026, 7, 3)


# 금형별 이벤트 이력. (월, 일, 시, 위치[, 설비명]) — 다섯 번째를 적으면 그
# 이벤트만 다른 설비에서 일어난 것이 된다(기본은 그 금형의 등록 설비).
#
# 이 표는 금형별로 적지만 **한 시트에 시간순으로 섞여** 들어간다. 실물이
# 그렇고, 그래야 "JIG ID 로 묶지 않으면 남의 이벤트로 구간이 닫힌다"는 사고를
# 샘플이 실제로 태운다.
#
# 핵심은 **위치가 바뀌는 순간**을 찾아내는 것이다. "설비" 로 들어간 행이 투입이고
# 바로 다음 행이 종료인데, 그 다음 행의 위치는 무엇이든 될 수 있다. 그래서
# 나가는 곳을 일부러 흩어 놓았다: 내부 수리·외부 수리·통합 Jig Room·사용 대기
# 보관함·반납 대기 보관함·폐기. 한 종류만 쓰면 "수리로 나가는 경우"처럼 흔한
# 전환이 검증되지 않는다.
#
# 각 줄 끝 주석이 곧 그 행이 검증하는 것이다.
EVENTS: dict[str, list[tuple]] = {
    # 수리 왕복 + 96시간 정확히. 네 번 투입된다.
    "#RX39513": [
        (6, 17, 8, "입고 대기 보관함"),
        (6, 17, 8, "통합 Jig Room"),
        (7, 1, 7, "설비"),              # run1 시작
        (7, 5, 7, "내부 수리"),          # run1 끝 96.0h → 4일(07-05 는 제외)
        (7, 5, 15, "사용 대기 보관함"),
        (7, 6, 9, "설비"),              # run2 시작
        (7, 6, 21, "통합 Jig Room"),     # run2 끝 12h → 1일
        # ★ 다른 금형(#RX28312)의 설비로 옮겨가 돈다. 이 구간만 설비코드가
        #   21004781 이어야 하고, 금형번호는 여전히 RX39513 이어야 한다.
        (7, 9, 8, "설비", BY_ID["#RX28312"].equip_name),   # run3 시작
        (7, 10, 8, "외부 수리"),         # run3 끝 24h 정확 → 1일
        (7, 12, 10, "사용 대기 보관함"),
        (7, 15, 6, "설비"),             # run4 시작
        (7, 15, 18, "반납 대기 보관함"),  # run4 끝 12h → 대기중으로 마무리
    ],
    # **설비 → 설비 연속**. 나가는 기록 없이 재투입된 경우 앞 구간이 거기서
    # 끝나고 새 구간이 시작돼야 한다 — 한 덩어리로 뭉치면 안 된다.
    "#RX28312": [
        (6, 18, 9, "입고 대기 보관함"),
        (6, 18, 9, "통합 Jig Room"),
        (7, 2, 8, "설비"),              # run1 시작
        (7, 3, 9, "설비"),              # ★ run1 끝(24.5h→2일)이자 run2 시작
        (7, 4, 9, "내부 수리"),          # run2 끝 24h → 1일
        (7, 6, 14, "사용 대기 보관함"),
        (7, 8, 7, "설비"),              # run3
        (7, 8, 19, "통합 Jig Room"),     # run3 끝 12h
        (7, 11, 6, "설비"),             # run4
        (7, 11, 20, "반납 대기 보관함"),  # run4 끝 14h
        (7, 13, 9, "사용 대기 보관함"),
    ],
    # 수리를 여러 번 오간다. 마지막이 수리라 상태가 '수리중' 이어야 한다.
    "#RX28315": [
        (6, 20, 10, "입고 대기 보관함"),
        (6, 20, 10, "통합 Jig Room"),
        (7, 8, 6, "설비"),              # run1
        (7, 8, 18, "내부 수리"),         # run1 끝 12h
        (7, 9, 8, "사용 대기 보관함"),
        (7, 9, 14, "설비"),             # run2
        (7, 10, 2, "내부 수리"),         # run2 끝 12h (자정 넘김)
        (7, 11, 9, "외부 수리"),         # 수리 → 수리 (설비 아님끼리의 전환)
        (7, 13, 11, "사용 대기 보관함"),
        (7, 14, 6, "설비"),             # run3
        (7, 16, 6, "내부 수리"),         # run3 끝 48h → 2일. 마지막이 수리
    ],
    # 자정을 넘기지만 24시간 미만인 구간을 여러 번 — 자정이 아니라 24시간이
    # 기준임을 확인한다.
    "#RX50177": [
        (6, 22, 8, "입고 대기 보관함"),
        (6, 22, 8, "통합 Jig Room"),
        (7, 10, 22, "설비"),            # run1
        (7, 12, 3, "사용 대기 보관함"),   # run1 끝 29h → 2일
        # ★ 기준정보에 없는 설비명. 금형은 남고 JIG ID 행(21005120)으로
        #   폴백하되 unknown_equipment 에 이 이름이 떠야 한다.
        (7, 13, 23, "설비", UNKNOWN_EQUIP),   # run2
        (7, 14, 3, "통합 Jig Room"),     # run2 끝 4h — 자정 넘겨도 1일
        (7, 16, 21, "설비"),            # run3
        (7, 17, 20, "내부 수리"),        # run3 끝 23h → 1일
        (7, 18, 10, "사용 대기 보관함"),
        (7, 19, 22, "설비"),            # run4
        (7, 20, 23, "반납 대기 보관함"),  # run4 끝 25h → 2일
    ],
    # **마지막이 설비** — 아직 가동 중이라 종료가 없다. 불량율이 비는 이유가
    # '조인 실패'가 아니라 '아직 안 끝남'임이 구분돼야 한다.
    "#RX41194": [
        (6, 25, 9, "입고 대기 보관함"),
        (6, 25, 9, "통합 Jig Room"),
        (7, 5, 8, "설비"),              # run1
        (7, 6, 8, "내부 수리"),          # run1 끝 24h
        (7, 7, 10, "사용 대기 보관함"),
        (7, 9, 7, "설비"),              # run2
        (7, 9, 19, "통합 Jig Room"),     # run2 끝 12h
        (7, 11, 8, "설비"),             # run3
        (7, 12, 8, "반납 대기 보관함"),   # run3 끝 24h
        (7, 13, 9, "사용 대기 보관함"),
        (7, 14, 9, "설비"),             # ★ run4 — 종료 이벤트가 없다
    ],
    # **폐기로 끝난다** — 상태가 '폐기' 여야 한다.
    "#RX39002": [
        (6, 28, 8, "입고 대기 보관함"),
        (6, 28, 8, "통합 Jig Room"),
        (7, 16, 7, "설비"),             # run1
        (7, 20, 7, "내부 수리"),         # run1 끝 96h → 4일
        (7, 20, 15, "외부 수리"),
        (7, 21, 9, "사용 대기 보관함"),
        (7, 22, 6, "설비"),             # run2
        (7, 22, 18, "통합 Jig Room"),    # run2 끝 12h
        (7, 23, 9, "내부 수리"),
        (7, 24, 10, "폐기"),            # ★ 마지막이 폐기
    ],
    # 기준정보에 없는 JIG ID — 이벤트가 아무리 많아도 MES 조회 키를 못 얻어
    # 목록에서 통째로 빠져야 한다.
    "#RX77777": [
        (6, 30, 8, "입고 대기 보관함"),
        (6, 30, 8, "통합 Jig Room"),
        (7, 6, 8, "설비"),
        (7, 7, 8, "내부 수리"),
        (7, 8, 10, "사용 대기 보관함"),
        (7, 9, 6, "설비"),
        (7, 9, 18, "통합 Jig Room"),
        (7, 11, 7, "설비"),
        (7, 12, 7, "반납 대기 보관함"),
        (7, 13, 9, "사용 대기 보관함"),
    ],
}

# MES 파일을 만들 날짜 범위. 위 사용구간을 전부 덮는다.
MES_DAYS = [date(2026, 7, d) for d in range(1, 26)]


def _new_book(app):
    book = app.books.add()
    for extra in list(book.sheets)[1:]:
        extra.delete()
    return book


def _save(book, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    book.save(str(path))
    book.close()


# ────────────────────────────────────────────────────────────────────
# JIG 기준정보 — 조인의 매핑표. 이게 없으면 금형번호도 설비코드도 못 얻는다.
# ────────────────────────────────────────────────────────────────────
_MASTER_HEADER = [
    "Shop명", "Line Group명", "Line명", "공정명", "공정구분",
    "JIG ID", "JIG명", "JIG 그룹명", "설비코드", "설비명",
]


def make_jig_master(app, path: Path) -> int:
    book = _new_book(app)
    sht = book.sheets[0]
    sht.name = "기준정보"

    sht.range("B2").value = _MASTER_HEADER
    rows = [
        [j.shop, j.line_group, j.line, j.process, None,
         j.jig_id, j.jig_name, None, j.equip_code, j.equip_name]
        for j in JIGS
    ]
    sht.range("B3").value = rows

    sht.range("B2:K2").api.Font.Bold = True
    sht.autofit()
    _save(book, path)
    return len(rows)


# ────────────────────────────────────────────────────────────────────
# JIG 관리대장 — 한 시트에 모든 금형. 시간순 이벤트 이력.
#
# 'JIG ID' 열이 그 행의 금형을 확정한다. 설비명은 식별이 아니라 그 구간의
# MES 조회 키를 고르는 데 쓴다.
# ────────────────────────────────────────────────────────────────────
_LEDGER_HEADER = [
    "JIG ID",
    "이벤트시간", "활동", "수량", "위치", "상태", "설비명", "사용 여부",
    "Shop(공정군)", "라인", "공정", "상세 구분1", "상세 구분2",
    "규격(Spec)", "규격 단위", "제조 업체", "생산 유형", "폐기 기준",
    "1차 경고 주기", "2차 경고 주기", "3차 경고 주기",
    "설명", "수정자 사번", "수정자",
]

# 설비에 들어가기 전/나온 뒤에 거치는 위치들. 실물의 어휘를 그대로 쓴다 —
# 파이프라인은 "설비" 하나만 알면 되고 나머지는 전부 '설비 아님'이다.
_BEFORE = ["입고 대기 보관함", "통합 Jig Room"]
_AFTER = ["내부 수리", "사용 대기 보관함", "반납 대기 보관함"]


def _ledger_rows() -> list[list]:
    """모든 금형의 이벤트를 한 시트에 시간순으로 섞어 넣는다.

    금형별로 뭉쳐 놓으면 "JIG ID 로 묶지 않아 남의 이벤트로 구간이 닫히는"
    사고를 샘플이 안 태운다. 실물도 시간순으로 적힌다.
    """
    rows = []
    for jig in [*JIGS, UNREGISTERED]:
        # 관리대장의 '라인' 은 공장 접두사가 없다("Pouch #10(S)"). 기준정보의
        # Line명은 붙어 있다("톈진 Pouch #10(S)"). 실물이 그렇고, 그래서 라인
        # 문자열끼리 직접 비교하면 안 된다 — MES 조회는 설비코드로 해야 한다.
        ledger_line = jig.line.split(None, 1)[1]

        for month, day, hour, location, *override in EVENTS[jig.jig_id]:
            # 정각으로 둔다. 분·초를 흩으면 24시간·96시간 **정확히** 걸친 구간이
            # 96.5h 가 되어 하루가 더 붙는다 — 올림 규칙의 경계가 곧 이 샘플의
            # 핵심 검증 대상인데 그게 뭉개진다.
            when = datetime(2026, month, day, hour)
            equipment = override[0] if override else jig.equip_name
            rows.append([
                jig.jig_id,
                when, None, None, location, None, equipment, "사용",
                jig.shop, ledger_line, "Pouch",
                *([None] * 13),
            ])
    rows.sort(key=lambda r: r[1])
    return rows


def make_jig_ledger(app, path: Path) -> dict:
    book = _new_book(app)
    sht = book.sheets[0]
    # 시트 이름은 아무 의미가 없다 — 금형을 확정하는 것은 'JIG ID' 열이다.
    sht.name = "관리대장"

    sht.range("B2").value = _LEDGER_HEADER
    rows = _ledger_rows()
    sht.range("B3").value = rows
    sht.range("B2:Y2").api.Font.Bold = True
    # JIG ID 가 B 열로 들어와 이벤트시간은 C 열이다.
    sht.range("C3:C%d" % (2 + len(rows))).number_format = "yyyy-mm-dd hh:mm:ss"
    sht.autofit()

    _save(book, path)
    return {"sheets": 1, "rows": len(rows)}


# ────────────────────────────────────────────────────────────────────
# MES — 하루 한 파일. 금형번호가 없고 (라인, 설비코드) 로만 식별된다.
#
# ※ 이 양식은 확정이 아니다. 모듈 docstring 의 '주의' 참고.
# ────────────────────────────────────────────────────────────────────
_MES_GROUPS = ["종합", "조립라인", "조립별화성"]
_MES_METRICS = ["이탈율(PPM)", "불량율(PPM)", "Loss율(PPM)", "재작업율(%)"]
_MES_FLAT = ["공장", "대표라인", "라인", "설비코드", "설비명",
             "투입수량", "양품수량", "불량수량"]


def _daily_counts(jig: Jig, day: date) -> tuple[int, int]:
    """(투입수량, 불량수량). 날짜·설비코드로 결정되는 값이라 재실행해도 같다."""
    seed = (day.toordinal() * 31 + jig.equip_code) % 97
    produced = 8_000 + seed * 37
    defects = 20 + (seed * 7) % 180
    return produced, defects


def make_mes_daily(app, path: Path, day: date) -> int:
    book = _new_book(app)
    sht = book.sheets[0]
    sht.name = "불량현황"

    # 날짜는 표가 아니라 상단 라벨에 있다 — key_values 로 잡혀 모든 행의
    # 기본값이 되어야 한다. 파일명에만 두면 시트만 보고는 알 수 없다.
    sht.range("B2").value = "날짜"
    sht.range("C2").value = f"{day:%Y.%m.%d}-{day:%Y.%m.%d}"

    # 2단 병합 헤더: 4행에 그룹명, 5행에 지표명.
    top = list(_MES_FLAT) + [None] * (len(_MES_GROUPS) * len(_MES_METRICS))
    bottom = [None] * len(_MES_FLAT)
    col = len(_MES_FLAT)
    for group in _MES_GROUPS:
        top[col] = group
        bottom.extend(_MES_METRICS)
        col += len(_MES_METRICS)

    sht.range("B4").value = top
    sht.range("B5").value = bottom

    # 그룹 헤더 가로 병합 + 평면 헤더 세로 병합.
    start = 2 + len(_MES_FLAT)  # B=2 기준 열 인덱스
    for i in range(len(_MES_GROUPS)):
        left = start + i * len(_MES_METRICS)
        right = left + len(_MES_METRICS) - 1
        sht.range((4, left), (4, right)).merge()
    for c in range(2, 2 + len(_MES_FLAT)):
        sht.range((4, c), (5, c)).merge()

    rows = []
    tot_produced = tot_defects = 0
    for jig in JIGS:
        produced, defects = _daily_counts(jig, day)
        good = produced - defects
        tot_produced += produced
        tot_defects += defects
        defect_ppm = round(defects / produced * PPM)
        rows.append([
            "천안 소형 CELL", jig.line_group, jig.line, jig.equip_code,
            jig.equip_name, produced, good, defects,
            # 종합
            round(defect_ppm * 1.14), defect_ppm, round(defect_ppm * 0.14), 0,
            # 조립라인
            round(defect_ppm * 0.68), round(defect_ppm * 0.54),
            round(defect_ppm * 0.13), 0,
            # 조립별화성
            round(defect_ppm * 0.46), round(defect_ppm * 0.45),
            round(defect_ppm * 0.01), 0,
        ])

    # TOTAL 행 — 라인이 아니므로 조인에서 빠져야 한다.
    total_ppm = round(tot_defects / tot_produced * PPM)
    rows.append([
        "TOTAL", None, None, None, None,
        tot_produced, tot_produced - tot_defects, tot_defects,
        round(total_ppm * 1.14), total_ppm, round(total_ppm * 0.14), 0,
        round(total_ppm * 0.68), round(total_ppm * 0.54),
        round(total_ppm * 0.13), 0,
        round(total_ppm * 0.46), round(total_ppm * 0.45),
        round(total_ppm * 0.01), 0,
    ])

    sht.range("B6").value = rows
    sht.range("B2").api.Font.Bold = True
    sht.range("B4:U5").api.Font.Bold = True
    sht.autofit()
    _save(book, path)
    return len(rows)


# ────────────────────────────────────────────────────────────────────
# IQC — 한 시트에 카테고리 2개 × (요약표 + 상세표) = 표 4개.
#       대장 상세는 2단 병합 헤더.
# ────────────────────────────────────────────────────────────────────
def make_iqc(app, path: Path) -> dict:
    book = _new_book(app)
    sht = book.sheets[0]
    sht.name = "Sheet1"

    # ── 카테고리 A: 측정 이력 ──────────────────────────────────────
    sht.range("B2").value = "Stack 금형 측정 이력"

    # A-1) 요약표(유형1/유형2). role="summary" 로 잡혀야 한다.
    sht.range("B4").value = ["구분", "항목", "수량"]
    sht.range("B5").value = [
        ["유형1", "양극 성형", 3],
        ["유형1", "음극 성형", 2],
        ["유형1", "음극 절단", 1],
        ["유형1", "소계", 6],
        ["유형2", "상면 성형", 2],
        ["유형2", "하면 성형", 2],
        ["유형2", "상면 절단", 1],
        ["유형2", "하면 절단", 1],
        ["유형2", "소계", 6],
        ["총계", "", 12],
    ]

    # A-2) 상세표. 관리번호에 # 접두사가 붙는다.
    sht.range("B17").value = [
        "No", "모델", "기종", "1차 명칭", "관리 번호", "업체", "26년 측정일"
    ]
    sht.range("B18").value = [
        [1, "양극 성형", "H104", "1차 양극 Notching", "#RX41194", "일신", date(2026, 3, 11)],
        [2, "음극 성형", "H104", "1차 음극 Notching", "#RX28312", "대성", date(2026, 4, 2)],
        [3, "음극 절단", "H104", "2차 음극 Cutting", "#RX28315", "일신", date(2026, 4, 19)],
        [4, "상면 성형", "H210", "1차 상면 Forming", "#RX50177", "삼우", date(2026, 5, 8)],
        [5, "하면 성형", "H210", "1차 하면 Forming", "#RX39002", "대성", date(2026, 5, 30)],
    ]

    # ── 카테고리 B: 측정 대장 ──────────────────────────────────────
    sht.range("B25").value = "Stack H104 금형 측정 대장"

    # B-1) 월별 피벗 요약표. 역시 role="summary".
    months = [f"{m}월" for m in range(1, 13)]
    sht.range("B27").value = ["구분", "내용"] + months + ["합계"]
    sht.range("B28").value = [
        ["입고검사", "검사 수량", 4, 6, 9, 7, 8, 11, 9, 0, 0, 0, 0, 0, 54],
        ["입고검사", "불량 수량", 0, 1, 0, 1, 0, 2, 1, 0, 0, 0, 0, 0, 5],
        ["입고검사", "불량률", 0, 0.167, 0, 0.143, 0, 0.182, 0.111, 0, 0, 0, 0, 0, 0.093],
    ]

    # B-2) 상세표 — 2단 병합 헤더.
    #   행 33: 상단 헤더 (성형부/절단부가 병합)
    #   행 34: 하단 헤더
    #   행 35~: 데이터
    top = [
        "No", "입고 시간", "금형 번호",
        "성형부", "", "", "",
        "절단부", "",
        "측정자", "조립자", "출고 시간",
        "PUNCH", "DIE", "차이", "간극",
        "측정 결과 PUNCH", "측정 결과 DIE", "연마자", "NG 사진",
    ]
    bottom = [
        "", "", "",
        "정극 성형", "부극 성형", "상면 성형", "하면 성형",
        "양극 절단", "음극 절단",
        "", "", "", "", "", "", "", "", "", "", "",
    ]
    sht.range("B33").value = top
    sht.range("B34").value = bottom
    # 병합: 성형부(E33:H33), 절단부(I33:J33)
    sht.range("E33:H33").merge()
    sht.range("I33:J33").merge()
    # 세로 병합(1단 헤더가 두 행에 걸침)
    for col in ("B", "C", "D", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U"):
        sht.range(f"{col}33:{col}34").merge()

    detail = [
        # No, 입고시간, 금형번호, 성형부4, 절단부2, 측정자, 조립자, 출고시간,
        # PUNCH, DIE, 차이, 간극, 결과P, 결과D, 연마자, NG사진
        [1, date(2026, 7, 1), "RX28312", "", "", "", "", "체크", "", "홍길동", "홍길동",
         date(2026, 7, 1), 12.48, 12.11, 0.37, 0.05, "A", "A", "구군", ""],
        [2, date(2026, 7, 3), "RX28315", "체크", "", "", "", "", "", "김영수", "홍길동",
         date(2026, 7, 3), 9.02, 8.71, 0.31, 0.04, "A", "B", "구군", ""],
        [3, date(2026, 7, 8), "RX50177", "", "", "체크", "", "", "", "홍길동", "박민",
         date(2026, 7, 9), 15.10, 14.66, 0.44, 0.06, "B", "A", "이철", "NG_0708.jpg"],
        [4, date(2026, 7, 14), "RX41194", "", "체크", "", "", "", "", "박민", "박민",
         date(2026, 7, 14), 11.75, 11.40, 0.35, 0.05, "A", "A", "구군", ""],
        [5, date(2026, 7, 18), "RX28312", "", "", "", "", "", "체크", "김영수", "홍길동",
         date(2026, 7, 19), 12.51, 12.09, 0.42, 0.05, "A", "A", "이철", ""],
        # 기준정보에 없는 금형 — orphan_mold_nos 에 잡힌다.
        [6, date(2026, 7, 21), "RX99999", "체크", "", "", "", "", "", "박민", "박민",
         date(2026, 7, 21), 8.30, 8.02, 0.28, 0.03, "A", "A", "구군", ""],
        # 소계 행 — 금형번호 칸에 금형이 아닌 값이 온다.
        ["", "", "소계", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
    ]
    sht.range("B35").value = detail

    for addr in ("B4:D4", "B17:H17", "B27:P27", "B33:U34"):
        sht.range(addr).api.Font.Bold = True
    sht.autofit()

    _save(book, path)
    return {"tables": 4, "detail_rows": len(detail)}


# ────────────────────────────────────────────────────────────────────
# PQC — 금형번호가 없다. 2단계에서 (날짜, 공정, 호기, 시간) 으로 조인한다.
# ────────────────────────────────────────────────────────────────────
def make_pqc(app, path: Path) -> int:
    book = _new_book(app)
    sht = book.sheets[0]
    sht.name = "공정불량"

    sht.range("B2").value = "PQC 공정 불량 이력 (2026-07)"

    sht.range("B4").value = [
        "날짜", "공정", "기종", "호기", "시간", "Lot", "Cell ID", "PO", "등급",
        "불량현상", "(Burr)위치", "(Burr)방향", "원인", "대책", "결과 확인",
        "작업자", "책임반장", "불량 사진",
    ]

    rows = [
        ["2026.07.02", "양극 성형", "H104", "2", "10:15", "LOT-2607-021", "CID-88412",
         "PO-5512", "B", "Burr", "상단", "좌", "펀치 마모", "연마 후 재측정", "완료",
         "김영수", "홍길동", ""],
        ["2026.07.05", "음극 절단", "H104", "5", "14:30", "LOT-2607-054", "CID-88790",
         "PO-5518", "C", "Burr", "하단", "우", "다이 간극 과다", "간극 조정", "완료",
         "박민", "홍길동", "NG_0705.jpg"],
        ["2026.07.11", "양극 절단", "H210", "4", "08:30", "LOT-2607-111", "CID-89033",
         "PO-5530", "B", "미성형", "중앙", "-", "가압력 부족", "가압 조건 변경", "진행중",
         "김영수", "이철", ""],
        ["2026.07.17", "음극 성형", "H104", "2", "16:45", "LOT-2607-170", "CID-89511",
         "PO-5544", "B", "Burr", "상단", "우", "펀치 마모", "펀치 교체", "완료",
         "홍길동", "홍길동", ""],
        ["2026.07.23", "양극 성형", "H104", "5", "20:10", "LOT-2607-231", "CID-90102",
         "PO-5559", "A", "스크래치", "측면", "-", "이물 혼입", "청소 주기 단축", "완료",
         "박민", "이철", "NG_0723.jpg"],
    ]
    sht.range("B5").value = rows
    sht.range("B4:S4").api.Font.Bold = True
    sht.autofit()

    _save(book, path)
    return len(rows)


def main() -> int:
    try:
        app = xw.App(visible=False, add_book=False)
    except Exception as exc:  # noqa: BLE001
        print(f"Excel 을 띄우지 못했습니다: {exc}")
        print("이 스크립트는 xlwings(실제 Excel)를 씁니다 — Excel 설치가 필요합니다.")
        return 1

    app.display_alerts = False
    app.screen_updating = False
    try:
        jobs = [
            ("EES", _UPLOADS / "EES" / "JIG_관리대장.xlsx", make_jig_ledger),
            ("JIG기준정보", _UPLOADS / "JIG기준정보" / "JIG_기준정보.xlsx", make_jig_master),
            ("IQC", _UPLOADS / "IQC" / "2026_금형측정대장.xlsx", make_iqc),
            ("PQC", _UPLOADS / "PQC" / "2026-07_공정불량.xlsx", make_pqc),
        ]
        for name, path, fn in jobs:
            result = fn(app, path)
            print(f"{name:12} {path.relative_to(_BACKEND_ROOT)}  {result}")

        mes_dir = _UPLOADS / "MES"
        for stale in mes_dir.glob("*.xlsx"):
            stale.unlink()
        written = [d for d in MES_DAYS if d != MES_MISSING_DAY]
        for day in written:
            make_mes_daily(app, mes_dir / f"{day:%Y-%m-%d}_불량현황.xlsx", day)
        print(f"{'MES':12} {mes_dir.relative_to(_BACKEND_ROOT)}  "
              f"{len(written)}개 파일 ({written[0]} ~ {written[-1]}, "
              f"{MES_MISSING_DAY} 일부러 빠뜨림)")
    finally:
        app.quit()

    print()
    print("폴더 매핑(.env 의 INGEST_STAGE_DIRS)이 아래와 맞아야 한다:")
    print("  EES:ees, JIG기준정보:jig_master, MES:mes, IQC:iqc, PQC:pqc")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
