"""금형 대시보드 조회 API.

`Depends(get_app_state)` 를 쓰지 않는다 — 데이터가 코드 안의 샘플이라 settings/DB
가 필요 없다. 서브프로젝트 ④(영속화)에서 DB 가 필요해지면 그때 의존성을 추가한다.
"""
from fastapi import APIRouter, HTTPException

from app.molds.query import filter_options, get_mold, list_molds
from app.molds.schemas import FilterOptions, MoldDetail, MoldStatus, MoldSummary

router = APIRouter()


# 주의: 이 라우트는 /molds/{mold_no} 보다 **먼저** 선언해야 한다. FastAPI 는
# 선언 순서대로 매칭하므로, 순서가 뒤바뀌면 "filters" 가 금형 번호로 잡혀
# 404 가 난다.
@router.get("/molds/filters", response_model=FilterOptions)
async def get_filter_options() -> FilterOptions:
    return filter_options()


@router.get("/molds", response_model=list[MoldSummary])
async def get_molds(
    status: MoldStatus | None = None,
    line: str | None = None,
    machine: str | None = None,
    q: str | None = None,
) -> list[MoldSummary]:
    return list_molds(status=status, line=line, machine=machine, q=q)


@router.get("/molds/{mold_no}", response_model=MoldDetail)
async def get_mold_detail(mold_no: str) -> MoldDetail:
    mold = get_mold(mold_no)
    if mold is None:
        raise HTTPException(status_code=404, detail="mold not found")
    return mold
