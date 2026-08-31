"""읽어들인 readings 를 parquet 으로 캐시한다 — 원본이 안 바뀌면 다시 안 읽는다.

왜 필요한가. 실행마다 원본을 처음부터 파싱한다. xlsx 경로는 그때마다 Excel 을
COM 으로 띄우는데, 파이프라인에서 가장 느리고 가장 잘 깨지는 구간이다(유령
EXCEL.EXE·DRM·STA 스레드 고정). 원본이 그대로라면 그 값을 다시 치를 이유가 없다.

왜 parquet 인가. dtype 이 파일 안에 들어 있다. csv·jsonl 로 캐시하면 읽을 때
dtype 을 다시 지정해야 하고, 안 하면 item_id 가 int64 로 돌아와 사전 조인이 예외
없이 전부 미매칭된다 — 이 저장소가 이미 두 번 겪은 실패다. 캐시가 그 실패를
되살리면 캐시를 안 쓰느니만 못하다. 실측(500K행·54항목 분단위):

    포맷      크기     전체읽기   두 열만   읽은 뒤 item_id
    csv     25.9MB    0.27s    0.14s    int64  (읽을 때 지정해야 str)
    jsonl   49.8MB    1.10s    1.07s    int64  (파일엔 문자열로 적혀 있는데도)
    parquet  0.6MB    0.05s    0.01s    str

jsonl 이 csv 보다 큰 것은 키 이름이 행마다 반복되기 때문이다(이 스키마에서 행당
54바이트 = 500K행이면 26MB 가 순수 낭비다).

캐시 파일은 하나다. 입력을 바꾸면 스탬프가 안 맞아 다시 만든다 — 두 파일을
번갈아 보면 매번 재생성되지만, 그게 A 를 물어봤는데 B 의 캐시를 주는 것보다 낫다.
"""
import json
import warnings
from pathlib import Path

import pandas as pd

from app.coating import parse

CACHE_NAME = "readings.parquet"
STAMP_NAME = "readings.stamp.json"

# 파싱 결과의 모양이 바뀌면(별칭 표·_finalize·사전 스키마) 옛 캐시를 읽으면 안
# 된다. 코드 변경을 자동으로 감지할 방법은 없으니 사람이 올린다.
_STAMP_VERSION = 1


def _file_key(path) -> dict:
    """파일이 그대로인지 볼 최소 정보. 내용 해시는 안 쓴다 — 수백 MB 를 매번
    읽으면 캐시로 아낀 시간을 그대로 다시 쓴다."""
    st = Path(path).stat()
    return {
        "path": str(Path(path).resolve()),
        "mtime_ns": st.st_mtime_ns,
        "size": st.st_size,
    }


def _stamp(input_path, dict_path, encodings, force_encoding, source, sheet) -> dict | None:
    """캐시가 유효한지 판단할 근거 전부. 파일이 없으면 None(캐시를 건너뛴다)."""
    try:
        return {
            "version": _STAMP_VERSION,
            "input": _file_key(input_path),
            # 사전도 조인해서 넣으므로 사전이 바뀌면 캐시도 낡는다.
            "dict": _file_key(dict_path),
            "format": source,
            "sheet": sheet,
            # 인코딩이 다르면 같은 바이트가 다른 글자가 된다. 여기 없으면
            # --encoding 을 바꿔도 옛 캐시가 그대로 나온다.
            "encodings": list(encodings) if encodings else None,
            "force_encoding": force_encoding,
        }
    except OSError:
        # 입력이 없는 경우다. 여기서 죽지 않고 load_readings 가 제 문장으로
        # 말하게 둔다 — 파일이 없다는 안내는 이미 거기가 더 잘한다.
        return None


def _matches(stamp_path: Path, want: dict) -> bool:
    try:
        return json.loads(stamp_path.read_text(encoding="utf-8")) == want
    except (OSError, ValueError):
        # 없거나 깨졌으면 안 맞는 것이다. 캐시 미스는 사고가 아니다.
        return False


def _save(cache_dir: Path, readings: pd.DataFrame, stamp: dict) -> None:
    """캐시 저장은 실패해도 리포트를 막지 않는다 — 캐시는 속도지 정답이 아니다.
    데이터 루트가 읽기 전용 공유 폴더인 경우가 있다.

    parquet 을 먼저 쓰고 스탬프를 나중에 쓴다. 중간에 죽으면 스탬프가 없거나
    옛것이라 다음 실행이 다시 만든다 — 틀린 방향(옛 데이터를 새것이라 믿는 것)
    으로는 깨지지 않는다.
    """
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        readings.to_parquet(cache_dir / CACHE_NAME, index=False)
        (cache_dir / STAMP_NAME).write_text(
            json.dumps(stamp, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:  # noqa: BLE001 - 캐시 실패로 리포트를 막지 않는다
        warnings.warn(f"중간 산출물을 쓰지 못했다({e}). 리포트는 계속한다.", stacklevel=2)


def cached_readings(
    input_path,
    dict_path=None,
    encodings=None,
    force_encoding=None,
    *,
    source="csv",
    sheet=None,
    cache_dir,
    refresh=False,
) -> tuple[pd.DataFrame, bool]:
    """readings 와 '캐시를 썼는지' 를 돌려준다.

    두 번째 값을 돌려주는 이유는 CLI 가 그것을 말해야 하기 때문이다. 캐시가
    조용히 동작하면, 원본을 고쳤는데 결과가 그대로일 때 사람이 캐시를 의심하지
    못한다.
    """
    dict_path = dict_path or parse.DEFAULT_DICT_PATH
    cache_dir = Path(cache_dir)
    want = _stamp(input_path, dict_path, encodings, force_encoding, source, sheet)

    if want is not None and not refresh and _matches(cache_dir / STAMP_NAME, want):
        try:
            return pd.read_parquet(cache_dir / CACHE_NAME), True
        except Exception:  # noqa: BLE001 - 깨진 캐시는 고칠 게 아니라 다시 만든다
            pass

    readings = parse.load_readings(
        input_path, dict_path, encodings, force_encoding, source=source, sheet=sheet
    )
    if want is not None:
        _save(cache_dir, readings, want)
    return readings, False
