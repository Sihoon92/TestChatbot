"""중간 산출물(parquet) 캐시 — 빨라지는 것보다 틀리지 않는 것이 먼저다.

캐시의 유일한 위험은 "원본이 바뀌었는데 옛 결과를 준다" 이고, 그건 예외 없이
조용히 일어난다. 그래서 무효화 조건 하나하나를 테스트로 고정한다.
"""
import shutil
from pathlib import Path

import pandas as pd
import pytest

from app.coating import interim, parse, report
from app.coating import schemas as S
from app.config import get_settings

SAMPLE = Path(__file__).parent / "fixtures" / "coating" / "sample_long.csv"
DICT = parse.DEFAULT_DICT_PATH


@pytest.fixture
def src(tmp_path):
    """수정할 수 있는 원본 사본. 픽스처 원본은 건드리지 않는다."""
    p = tmp_path / "원본.csv"
    shutil.copy(SAMPLE, p)
    return p


def load(src, cache_dir, **kw):
    return interim.cached_readings(src, DICT, cache_dir=cache_dir, **kw)


# ── 기본 동작 ───────────────────────────────────────────────────────────

def test_first_call_builds_and_second_call_reuses(src, tmp_path):
    cache = tmp_path / "interim"
    first, used = load(src, cache)
    assert used is False
    assert (cache / interim.CACHE_NAME).exists()

    second, used = load(src, cache)
    assert used is True
    pd.testing.assert_frame_equal(first, second)


def test_cached_result_equals_direct_parse(src, tmp_path):
    """캐시에서 온 것과 방금 파싱한 것이 완전히 같아야 한다. 여기가 어긋나면
    '캐시를 쓸 때만 결과가 다른' 가장 재현하기 어려운 버그가 된다."""
    load(src, tmp_path / "interim")                      # 캐시 생성
    cached, used = load(src, tmp_path / "interim")
    assert used is True
    pd.testing.assert_frame_equal(cached, parse.load_readings(src, DICT))


def test_dtypes_survive_the_round_trip(src, tmp_path):
    """parquet 을 고른 유일한 이유다. csv·jsonl 로 캐시하면 item_id 가 int64 로
    돌아와 사전 조인이 예외 없이 전부 미매칭된다."""
    load(src, tmp_path / "interim")
    cached, _ = load(src, tmp_path / "interim")
    assert not pd.api.types.is_numeric_dtype(cached[S.ITEM])
    assert cached[S.AT].dtype.kind == "M"
    assert parse.unknown_item_ids(cached) == []


# ── 무효화 ──────────────────────────────────────────────────────────────

def test_edited_source_invalidates(src, tmp_path):
    """원본을 고쳤으면 다시 만들어야 한다."""
    cache = tmp_path / "interim"
    load(src, cache)
    text = src.read_text(encoding="utf-8-sig")
    src.write_text(text + text.splitlines()[1] + "\n", encoding="utf-8-sig")

    got, used = load(src, cache)
    assert used is False
    assert len(got) == 32          # 한 행 늘어난 것이 실제로 보인다


def test_different_input_file_does_not_reuse_the_other_cache(src, tmp_path):
    """A 를 물어봤는데 B 의 캐시를 주는 것이 이 기능의 최악이다. 캐시 파일이
    하나뿐이므로 경로가 스탬프에 들어가 있어야만 막힌다."""
    cache = tmp_path / "interim"
    load(src, cache)

    other = tmp_path / "다른원본.csv"
    other.write_text(
        "lot_id,worked_at,product,item_id,value\n"
        "L9,2026-02-01 09:00,OTHER,10030271,99\n",
        encoding="utf-8-sig",
    )
    got, used = load(other, cache)
    assert used is False
    assert list(got[S.LOT]) == ["L9"]


def test_changed_encoding_candidates_invalidate(src, tmp_path):
    """인코딩이 다르면 같은 바이트가 다른 글자가 된다. 스탬프에 없으면
    --encoding 을 바꿔도 옛 캐시가 그대로 나온다."""
    cache = tmp_path / "interim"
    load(src, cache, encodings=["utf-8-sig"])
    _, used = load(src, cache, encodings=["utf-8-sig", "cp949"])
    assert used is False


def test_changed_dictionary_invalidates(src, tmp_path):
    """사전은 조인해서 들어간다 — 사전이 바뀌면 결과도 바뀐다."""
    cache = tmp_path / "interim"
    my_dict = tmp_path / "사전.csv"
    shutil.copy(DICT, my_dict)
    interim.cached_readings(src, my_dict, cache_dir=cache)

    my_dict.write_text(
        my_dict.read_text(encoding="utf-8-sig") + "99999999,새 항목,input,control,\n",
        encoding="utf-8-sig",
    )
    _, used = interim.cached_readings(src, my_dict, cache_dir=cache)
    assert used is False


def test_refresh_rebuilds_even_when_stamp_matches(src, tmp_path):
    """원본을 덮어썼는데 mtime 이 그대로인 경우의 탈출구."""
    cache = tmp_path / "interim"
    load(src, cache)
    _, used = load(src, cache, refresh=True)
    assert used is False


def test_stamp_version_bump_invalidates(src, tmp_path, monkeypatch):
    """파싱 결과의 모양이 바뀌면(별칭 표·_finalize) 옛 캐시를 읽으면 안 된다.
    코드 변경은 자동으로 못 잡으니 사람이 올리는 번호가 실제로 동작해야 한다."""
    cache = tmp_path / "interim"
    load(src, cache)
    monkeypatch.setattr(interim, "_STAMP_VERSION", interim._STAMP_VERSION + 1)
    _, used = load(src, cache)
    assert used is False


# ── 고장나도 리포트를 막지 않는다 ────────────────────────────────────────

def test_corrupt_cache_falls_back_to_parsing(src, tmp_path):
    """깨진 캐시는 고칠 게 아니라 다시 만들 것이다."""
    cache = tmp_path / "interim"
    load(src, cache)
    (cache / interim.CACHE_NAME).write_bytes(b"not a parquet file")

    got, used = load(src, cache)
    assert used is False
    assert len(got) == 31


def test_unwritable_cache_dir_warns_but_still_returns(src, tmp_path, monkeypatch):
    """데이터 루트가 읽기 전용 공유 폴더인 경우가 있다. 캐시는 속도지 정답이
    아니므로, 못 써도 리포트는 나와야 한다."""
    def boom(*a, **kw):
        raise OSError("읽기 전용")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", boom)
    with pytest.warns(UserWarning, match="중간 산출물"):
        got, used = load(src, tmp_path / "interim")
    assert used is False
    assert len(got) == 31


def test_missing_input_still_reports_the_normal_error(tmp_path):
    """캐시 층이 파일 없음 안내를 가로채면 안 된다."""
    with pytest.raises((FileNotFoundError, ValueError)):
        load(tmp_path / "없는파일.csv", tmp_path / "interim")


# ── report 와의 배선 ────────────────────────────────────────────────────

def test_report_run_with_cache_matches_run_without(src, tmp_path, capsys):
    """캐시를 켜고 끈 결과가 달라지면 캐시가 아니라 두 번째 구현이다.

    무엇을 했는지도 말해야 한다. 캐시가 조용하면 원본을 고쳤는데 결과가 그대로일
    때 사람이 캐시를 의심하지 못한다. stdout 은 산출물 경로 두 줄이어야 하므로
    이 안내는 stderr 로 간다."""
    cache = tmp_path / "interim"
    plain = report.profile_dataset(src, DICT)

    report.run(src, DICT, out_dir=tmp_path / "a", cache_dir=cache)
    assert "재생성" in capsys.readouterr().err
    md, _ = report.run(src, DICT, out_dir=tmp_path / "b", cache_dir=cache)
    assert "사용" in capsys.readouterr().err

    # run() 이 넘기는 것과 같은 인코딩 후보여야 스탬프가 맞는다(.env 단일 출처).
    cached, used = interim.cached_readings(
        src, DICT, get_settings().coating_csv_encoding_list, cache_dir=cache
    )
    assert used is True
    assert report.profile_readings(cached)["verdict"] == plain["verdict"]
    assert Path(md).exists()


def test_run_without_cache_dir_writes_nothing(src, tmp_path):
    """라이브러리로 부를 때 남의 디렉터리에 파일을 만들지 않는다."""
    out = tmp_path / "out"
    report.run(src, DICT, out_dir=out)
    assert not (tmp_path / "interim").exists()
    assert list(out.iterdir())      # 리포트는 정상적으로 나왔다


def test_cli_no_cache_flag_disables_it(tmp_path):
    args = report.build_parser().parse_args([])
    assert args.cache is True and args.refresh is False
    assert report.build_parser().parse_args(["--no-cache"]).cache is False
    assert report.build_parser().parse_args(["--refresh"]).refresh is True
