"""테스트 전역 옵션.

--sample 로 픽스처가 아닌 실제 파일을 넣어 파이프라인을 검증한다.

사내 실데이터는 gitignore 대상(backend/data/)이라 저장소에 넣을 수 없고, 파일
이름도 사업부·추출일마다 다르다. 검사하려고 이름을 sample_long.csv 로 바꿔
덮어쓰게 하면 원본을 건드리는 셈이고, 어느 파일을 검사했는지도 남지 않는다.
이름 그대로 인자로 넘긴다.

기존 픽스처 테스트는 이 옵션의 영향을 받지 않는다. 그것들은 "이 데이터에서 이
값이 나온다" 를 고정하는 회귀 테스트라 다른 파일로 돌리면 전부 실패하는 게
정상이고 의미도 없다. --sample 은 값이 아니라 계약만 보는 스모크 테스트가 쓴다
(test_coating_sample_smoke.py).
"""
from pathlib import Path

import pytest

# 엑셀로 볼 확장자. 나머지는 CSV 로 읽는다.
_EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls"}


def pytest_addoption(parser):
    group = parser.getgroup("coating", "코팅 파이프라인 스모크 검사")
    group.addoption(
        "--sample",
        default=None,
        metavar="PATH",
        help="검사할 원본 파일(csv·xlsx). 생략하면 스모크 테스트를 건너뛴다.",
    )
    group.addoption(
        "--sample-sheet",
        default=None,
        metavar="NAME",
        help="--sample 이 xlsx 일 때 읽을 시트. 생략하면 첫 시트.",
    )


def pytest_configure(config):
    """경로를 여기서 한 번 검사한다.

    픽스처 안에서 fail 하면 테스트마다 같은 에러가 반복돼 다섯 줄이 된다.
    잘못된 CLI 인자는 테스트 실패가 아니라 사용법 오류이므로, 수집 전에 한 줄로
    끝낸다(pytest 가 자기 옵션을 틀렸을 때와 같은 방식). 조용히 skip 하면
    검사한 줄 알고 넘어가므로 skip 은 답이 아니다.
    """
    raw = config.getoption("--sample")
    if raw and not Path(raw).expanduser().exists():
        raise pytest.UsageError(f"--sample 파일이 없다: {raw}")


@pytest.fixture(scope="session")
def sample_path(pytestconfig) -> Path:
    """--sample 로 받은 파일. 존재 여부는 pytest_configure 가 이미 봤다."""
    raw = pytestconfig.getoption("--sample")
    if not raw:
        pytest.skip("--sample <경로> 로 검사할 원본 파일을 지정한다")
    return Path(raw).expanduser()


@pytest.fixture(scope="session")
def sample_source(sample_path: Path) -> str:
    """csv | xlsx. 확장자로 정한다 - 사람이 같은 것을 두 번 적게 하지 않는다."""
    return "xlsx" if sample_path.suffix.lower() in _EXCEL_SUFFIXES else "csv"


@pytest.fixture(scope="session")
def sample_sheet(pytestconfig) -> str | None:
    return pytestconfig.getoption("--sample-sheet") or None
