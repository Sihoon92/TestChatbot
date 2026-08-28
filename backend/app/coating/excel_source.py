"""xlsx 원본 → long 테이블 DataFrame.

왜 이 모듈이 있나. 사내 실데이터가 문서보안(DRM)으로 암호화돼 있어 python 이
파일 바이트를 직접 읽으면 암호문만 본다. DRM 은 등록된 애플리케이션 안에서만
복호화하므로, Excel 을 COM 으로 띄워 그 안에서 값을 꺼내오는 우회로가 필요하다.

COM 자체는 다루지 않는다 - `app.excel.workbook` 이 STA 스레드 고정, 유령
EXCEL.EXE 정리, COM 프록시가 예외에 실려 새는 문제까지 이미 해결해 뒀다.
여기서는 "표를 어떻게 해석할 것인가" 만 맡는다.

값은 전부 문자열로 돌려준다. 숫자·시각 변환은 CSV 경로와 공유하는 `parse` 의
정규화가 한다 - 두 입력 경로가 같은 계약으로 수렴해야 뒤 단계(pivot·events·
features)가 출처를 몰라도 된다.
"""
from pathlib import Path

import pandas as pd

from app.coating import schemas as S
from app.excel.grid import cell_to_text

# 엑셀 시트의 최대 행. 원본이 이보다 크면 xlsx 를 만드는 시점에 이미 잘린다.
_EXCEL_MAX_ROWS = 1_048_576

# 이 다섯 개가 없으면 뒤 단계가 KeyError 로 죽는다. item_name 은 없어도 된다
# (원본은 대부분 비어 있고 사전 것을 쓴다).
_REQUIRED = (S.LOT, S.AT, S.PRODUCT, S.ITEM, S.VALUE)


def read_long_table(path: str | Path, sheet: str | None = None) -> pd.DataFrame:
    """xlsx 를 Excel 로 열어 long 테이블로 읽는다. 모든 셀은 문자열.

    sheet 가 None 이면 첫 시트를 읽는다.
    """
    # 지연 import. 모듈 최상단에 두면 xlwings·pywin32 가 없는 최소 설치에서
    # CSV 경로까지 import 단계에서 죽는다.
    from app.excel.workbook import open_workbook

    with open_workbook(str(path)) as wb:
        names = wb.sheet_names()
        target = sheet or (names[0] if names else None)
        if target not in names:
            raise ValueError(
                f"시트를 찾을 수 없다: {target!r} ({path})\n"
                f"  이 파일의 시트: {names}\n"
                "  COATING_XLSX_SHEET 또는 --sheet 로 지정한다."
            )
        rows, _top_left = wb.used_values(target)

    if len(rows) >= _EXCEL_MAX_ROWS:
        # 정확히 한계값이면 원본이 잘렸다고 봐야 한다. 조용히 통과시키면
        # 데이터 일부가 사라진 채로 판정 리포트가 나온다.
        raise ValueError(
            f"행 수가 엑셀 한계({_EXCEL_MAX_ROWS})에 걸렸다: {path}\n"
            "  xlsx 로 만드는 시점에 원본이 잘렸을 가능성이 높다.\n"
            "  lot 단위로 파일을 나눠 만든다."
        )
    if len(rows) < 2:
        raise ValueError(f"데이터 행이 없다(헤더만 있거나 빈 시트다): {path}")

    header = [cell_to_text(c) for c in rows[0]]
    missing = [c for c in _REQUIRED if c not in header]
    if missing:
        raise ValueError(
            f"필수 컬럼이 없다: {missing} ({path})\n"
            f"  이 시트의 헤더: {header}\n"
            "  헤더가 첫 행에 오도록 맞추거나 --sheet 로 다른 시트를 지정한다."
        )

    body = [[cell_to_text(c) for c in row] for row in rows[1:]]
    return pd.DataFrame(body, columns=header, dtype=object)
