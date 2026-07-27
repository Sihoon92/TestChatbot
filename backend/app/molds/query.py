"""금형 조회·필터 로직.

지금은 코드 안의 고정 샘플(sample_data.SAMPLE_MOLDS)을 메모리에서 거른다.
서브프로젝트 ④(영속화)에서 이 세 함수의 내부만 DB 쿼리로 교체하면 되도록,
라우터·프론트가 의존하는 것은 함수 시그니처뿐이게 유지한다.
"""
from app.molds.sample_data import SAMPLE_MOLDS
from app.molds.schemas import (
    ALL_STATUSES,
    FilterOptions,
    Installation,
    MoldDetail,
    MoldStatus,
    MoldSummary,
)


def list_molds(
    *,
    status: MoldStatus | None = None,
    line: str | None = None,
    machine: str | None = None,
    q: str | None = None,
) -> list[MoldSummary]:
    """조건에 맞는 금형 요약 목록. 인자가 None 이면 그 조건으로 거르지 않는다."""
    result = [mold.summary for mold in SAMPLE_MOLDS]
    if status is not None:
        result = [s for s in result if s.status == status]
    if line is not None:
        result = [s for s in result if s.line == line]
    if machine is not None:
        result = [s for s in result if s.machine == machine]
    if q is not None:
        # 사용자가 붙여넣기로 공백을 흘리는 일이 흔해 앞뒤 공백을 떼고 비교한다.
        needle = q.strip().lower()
        if needle:
            result = [s for s in result if needle in s.mold_no.lower()]
    return result


def get_mold(mold_no: str) -> MoldDetail | None:
    """금형 번호로 상세를 찾는다. 없으면 None(라우터가 404 로 바꾼다)."""
    for mold in SAMPLE_MOLDS:
        if mold.summary.mold_no == mold_no:
            return mold
    return None


def filter_options() -> FilterOptions:
    """필터 드롭다운이 쓸 선택지.

    - statuses: 데이터에서 뽑지 않고 고정 4종을 돌려준다. 상태는 도메인 어휘이지
      데이터의 산물이 아니다 — '폐기' 금형이 지금 없다고 해서 '폐기'로 조회할
      수단이 사라지면 안 된다(결과 0건과 조회 불가는 다르다).
    - installations: 실제 존재하는 (라인, 호기) 쌍만. 독립 리스트로 주면 UI 가
      존재하지 않는 조합을 만들어낼 수 있다.
    """
    seen: list[tuple[str, str]] = []
    for mold in SAMPLE_MOLDS:
        line, machine = mold.summary.line, mold.summary.machine
        if line is None or machine is None:
            continue
        if (line, machine) not in seen:
            seen.append((line, machine))
    seen.sort()
    return FilterOptions(
        statuses=list(ALL_STATUSES),
        installations=[Installation(line=ln, machine=mc) for ln, mc in seen],
    )
