"""격자 + 레이아웃 → 행 목록. 파이프라인에서 가장 중요한 순수 함수다.

LLM 도 Excel 도 DB 도 모른다. 격자 리터럴과 레이아웃만 있으면 결과가
재현되므로, "왜 이 값이 이렇게 나왔지" 를 나중에 반드시 되짚을 수 있다.

금형 귀속은 하지 않는다 — IQC 는 금형번호 열로, PQC 는 (날짜·공정·호기·시간)
조인으로 귀속되는데, 그 차이가 여기 섞이면 읽을 수 없어진다. 여기서는
"레이아웃이 지목한 셀들을 필드명으로 담은 행" 까지만 만든다.
"""
from app.ingest.layout import cell_at, last_row_no
from app.ingest.normalize import cell_to_text
from app.ingest.schemas import Row, SheetLayout, TableBlock


def _cell_text(grid: list[list], top_left: str, cell: str) -> str | None:
    return cell_to_text(cell_at(grid, top_left, cell))


def _key_value_defaults(
    grid: list[list], top_left: str, layout: SheetLayout
) -> dict[str, str | None]:
    """시트 전역 키-값 블록. 모든 행의 기본값이 된다."""
    return {
        kv.field: _cell_text(grid, top_left, kv.value_cell)
        for kv in layout.key_values
    }


def _table_rows(
    grid: list[list],
    top_left: str,
    table: TableBlock,
    defaults: dict[str, str | None],
    layout: SheetLayout,
    source_file: str,
) -> list[Row]:
    # detail 표에 컬럼 매핑이 없으면 뽑을 것이 없다. 조용히 0행을 돌려주면
    # "표는 있는데 행이 없다" 는 상태가 되어 원인을 추적할 수 없다 —
    # TableBlock.columns 는 기본값이 [] 라 에이전트가 빠뜨려도 스키마 검증을
    # 통과하므로, 여기서 명시적으로 실패해야 파이프라인이 그 파일을 error 로
    # 표시하고 사유를 남길 수 있다.
    if not table.columns:
        raise ValueError(
            f"detail 표 '{table.name}' 에 컬럼 매핑이 없다 "
            f"(시트 '{layout.sheet_name}', {source_file})"
        )

    # 헤더 행 번호를 data_end_row 에 넣는 실수가 여기서 잡힌다. 그대로 두면
    # range() 가 비어 조용히 0행이 되고, "표는 있는데 행이 없다"는 추적
    # 불가능한 상태가 된다 — 컬럼 매핑 누락과 같은 성격의 실패다.
    if table.data_end_row is not None and table.data_end_row < table.data_start_row:
        raise ValueError(
            f"표 '{table.name}' 의 data_end_row({table.data_end_row}) 가 "
            f"data_start_row({table.data_start_row}) 보다 작다 "
            f"(시트 '{layout.sheet_name}', {source_file})"
        )

    rows: list[Row] = []
    # 격자 밖까지 훑지 않는다 — data_end_row 가 크게 잡혀도 없는 행을
    # 만들어내면 안 된다.
    hard_end = last_row_no(grid, top_left)
    # 아래 빈 행 판정과 같은 기준(is None)을 쓴다. truthy 검사로 두면
    # data_end_row=0 일 때 여기서는 "미지정", 아래에서는 "지정됨"이 되어
    # 두 곳이 모순된다.
    end = (
        min(table.data_end_row, hard_end)
        if table.data_end_row is not None
        else hard_end
    )

    for row_no in range(table.data_start_row, end + 1):
        values = {
            col.field: _cell_text(grid, top_left, f"{col.column}{row_no}")
            for col in table.columns
        }
        # 매핑된 칸이 전부 비었으면 표가 끝난 것이다. 계속 훑으면 표 아래의
        # 다른 블록(비고·서명란)이 금형인 척 딸려 들어온다.
        if all(v is None for v in values.values()):
            if table.data_end_row is None:
                break
            continue
        # 표 컬럼이 키-값과 같은 필드를 주면 표가 이긴다 — 행별 값이 더 구체적이다.
        merged = {**defaults, **values}
        rows.append(
            Row(
                source_file=source_file,
                sheet=layout.sheet_name,
                row_no=row_no,
                values=merged,
            )
        )
    return rows


def parse_rows(
    grid: list[list], top_left: str, layout: SheetLayout, source_file: str
) -> list[Row]:
    """detail 표들에서 행을 뽑는다.

    role="summary" 인 표는 건너뛴다. 실물 IQC 시트에는 summary 표가 두 개 있고,
    파싱하면 '소계'/'총계'가 금형번호로 읽힌다.

    표가 하나도 없으면(성적서형) 키-값 블록만으로 한 행을 만든다.
    """
    defaults = _key_value_defaults(grid, top_left, layout)
    detail_tables = [t for t in layout.tables if t.role == "detail"]

    if not detail_tables:
        if not defaults:
            return []
        return [
            Row(
                source_file=source_file,
                sheet=layout.sheet_name,
                row_no=1,
                values=defaults,
            )
        ]

    rows: list[Row] = []
    for table in detail_tables:
        rows.extend(
            _table_rows(grid, top_left, table, defaults, layout, source_file)
        )
    return rows
