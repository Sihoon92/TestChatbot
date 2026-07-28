"""수집 파이프라인 시험용 샘플 엑셀 3종을 만든다 (MES / IQC / PQC).

실물 파일을 구하기 전에 파이프라인을 끝까지 돌려보기 위한 것이다. 사용자가
설명한 실제 문서 구조를 최대한 그대로 재현했다 — 특히 IQC 는 한 시트에
카테고리 2개 × (요약표 + 상세표) = 표 4개, 2단 병합 헤더, 소계 행까지 넣었다.

실행 (backend/ 에서, venv 파이썬으로):
    .venv/Scripts/python.exe examples/make_sample_uploads.py

openpyxl 이 없는 환경이라 xlwings(실제 Excel)로 만든다. Excel 이 설치돼 있어야
하고, 만드는 동안 숨김 Excel 프로세스가 잠깐 뜬다.

## 일부러 넣어둔 것들 (파이프라인의 방어 장치를 눈으로 확인하려고)

- **표가 A1 에서 시작하지 않는다** (B2 부터). used_range 오프셋 처리를 탄다.
- **IQC 상세표에 소계 행**이 섞여 있다 → normalize_mold_no 의 _NOT_A_MOLD 가 거른다.
- **IQC 에 MES 에 없는 금형(RX99999)** 이 하나 있다 → orphan_mold_nos 에 잡힌다.
- **요약표에 "소계"/"총계" 행**이 있다 → 에이전트가 role="summary" 로 표시해야
  하고, 그러면 파서가 통째로 건너뛴다. 잘못 판정하면 "소계"가 금형번호로 읽힌다.
- **관리번호에 # 접두사** (#RX41194) → normalize_mold_no 가 떼어 대장의 RX41194 와
  같은 금형으로 합친다.
- MES 상태 어휘는 전부 STATUS_MAP 에 있는 값만 썼다. 인식 실패 경로를 보고 싶으면
  아무 행의 사용상태를 "가동" 같은 값으로 바꾸고 다시 수집해 보면 된다.

## 주의

PQC 는 1단계에서 읽지 않는다(pipeline._PROCESSED_KINDS 가 mes/iqc 뿐).
2단계에서 (날짜, 공정, 호기, 시간) 조인을 붙일 때 쓰려고 미리 만들어 둔다 —
그래서 PQC 의 그 네 값은 MES 의 특정 행들과 일치시켜 두었다.
"""
import sys
from datetime import date
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

import xlwings as xw

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_UPLOADS = _BACKEND_ROOT / "data" / "uploads"

# 금형 5종. MES 가 마스터이므로 여기 있는 것만 대시보드에 나온다.
# (라인, 호기) 는 사용중인 금형만 갖는다 — assemble 이 그 외에는 비운다.
MOLDS = [
    # 금형번호,   상태,     라인, 호기, 기종
    ("RX28312", "사용중", "3", "2", "H104"),
    ("RX28315", "사용중", "3", "5", "H104"),
    ("RX50177", "사용중", "1", "4", "H210"),
    ("RX41194", "대기중", "",  "",  "H104"),
    ("RX39002", "수리중", "",  "",  "H210"),
]

PROCESSES = ["양극 성형", "음극 성형", "양극 절단", "음극 절단"]
TIMES = ["08:30", "10:15", "14:30", "16:45", "20:10"]


def _new_book(app):
    book = app.books.add()
    for extra in list(book.sheets)[1:]:
        extra.delete()
    return book


# ────────────────────────────────────────────────────────────────────
# MES — 한 행 = 생산 이벤트 1건. 같은 금형이 여러 번 나온다.
# ────────────────────────────────────────────────────────────────────
def make_mes(app, path: Path) -> int:
    book = _new_book(app)
    sht = book.sheets[0]
    sht.name = "생산이벤트"

    sht.range("B2").value = "MES 생산 이벤트 조회 결과 (2026-07)"

    header = [
        "No", "날짜", "공정", "기종", "라인", "호기", "시간",
        "금형번호", "사용상태", "사용타수", "총설치횟수", "총생산수량", "불량율",
    ]
    sht.range("B4").value = header

    rows = []
    # 금형별 누적치. 뒤에 나온 행이 최신이므로 값이 커지도록 쌓는다.
    shots = {m[0]: 1200 for m in MOLDS}
    produced = {m[0]: 180_000 for m in MOLDS}
    installs = {m[0]: 5 for m in MOLDS}

    no = 0
    for day in range(1, 26):
        for t_idx, hhmm in enumerate(TIMES):
            mold_no, status, line, machine, kind = MOLDS[(day + t_idx) % len(MOLDS)]
            # 대기중·수리중 금형은 생산 이벤트가 드물다 — 실물에 가깝게 걸러낸다.
            if status != "사용중" and (day + t_idx) % 4 != 0:
                continue
            no += 1
            shots[mold_no] += 137
            produced[mold_no] += 4_200
            if day % 9 == 0 and t_idx == 0:
                installs[mold_no] += 1
            rows.append([
                no,
                date(2026, 7, day),
                PROCESSES[(day + t_idx) % len(PROCESSES)],
                kind,
                line,
                machine,
                hhmm,
                mold_no,
                status,
                shots[mold_no],
                installs[mold_no],
                produced[mold_no],
                round(0.004 + ((day * 7 + t_idx) % 23) * 0.0008, 4),
            ])

    sht.range("B5").value = rows
    sht.range("B4:N4").api.Font.Bold = True
    sht.range("C5:C%d" % (4 + len(rows))).number_format = "yyyy-mm-dd"
    sht.autofit()

    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(str(path))
    book.close()
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
        # MES 에 없는 금형 — orphan_mold_nos 에 잡힌다.
        [6, date(2026, 7, 21), "RX99999", "체크", "", "", "", "", "", "박민", "박민",
         date(2026, 7, 21), 8.30, 8.02, 0.28, 0.03, "A", "A", "구군", ""],
        # 소계 행 — 금형번호 칸에 금형이 아닌 값이 온다.
        ["", "", "소계", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
    ]
    sht.range("B35").value = detail

    for addr in ("B4:D4", "B17:H17", "B27:P27", "B33:U34"):
        sht.range(addr).api.Font.Bold = True
    sht.autofit()

    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(str(path))
    book.close()
    return {"tables": 4, "detail_rows": len(detail)}


# ────────────────────────────────────────────────────────────────────
# PQC — 금형번호가 없다. (날짜, 공정, 호기, 시간) 으로 MES 와 조인한다(2단계).
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

    # 아래 (날짜, 공정, 호기, 시간) 은 MES 에 같은 조합이 있도록 맞췄다 —
    # 2단계에서 조인이 실제로 맞는지 확인하는 용도다.
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

    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(str(path))
    book.close()
    return len(rows)


def main() -> int:
    targets = {
        "MES": (_UPLOADS / "MES" / "2026-07_생산이벤트.xlsx", make_mes),
        "IQC": (_UPLOADS / "IQC" / "2026_금형측정대장.xlsx", make_iqc),
        "PQC": (_UPLOADS / "PQC" / "2026-07_공정불량.xlsx", make_pqc),
    }

    try:
        app = xw.App(visible=False, add_book=False)
    except Exception as exc:  # noqa: BLE001
        print(f"Excel 을 띄우지 못했습니다: {exc}")
        print("이 스크립트는 xlwings(실제 Excel)를 씁니다 — Excel 설치가 필요합니다.")
        return 1

    app.display_alerts = False
    app.screen_updating = False
    try:
        for name, (path, fn) in targets.items():
            if path.exists():
                path.unlink()
            result = fn(app, path)
            print(f"{name:4} {path.relative_to(_BACKEND_ROOT)}  {result}")
    finally:
        app.quit()

    print()
    print("이제 대시보드에서 [수집 실행] 을 누르면 된다.")
    print("PQC 는 1단계에서 읽지 않는다(2단계 조인용으로 미리 만들어 둠).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
