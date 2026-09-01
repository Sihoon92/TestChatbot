"""중간 산출물 덤프 — 검사용이지 캐시가 아니다.

덤프는 쓰기 전용이다. 어떤 경로도 이 파일들을 읽어 입력으로 되쓰지 않는다.
그래서 스탬프도 무효화 로직도 없고, 그게 걷어낸 interim 캐시(e287ef8 →
8ac3ae0)와 이 기능이 갈리는 지점이다. 캐시는 "이 파일이 아직 유효한가" 를
매번 답해야 하지만 덤프는 그 질문 자체가 없다.

여기서 거는 것은 둘이다. 파일이 실제로 생기는가, 그리고 그 안이 메모리에서
계산된 것과 같은가.
"""
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.coating import dump, parse, report
from app.coating import schemas as S
from app.config import get_settings

SAMPLE = Path(__file__).parent / "fixtures" / "coating" / "sample_long.csv"
DICT = parse.DEFAULT_DICT_PATH


def _readings_with_two_events():
    """gap 을 두 번(올림·내림) 만지고 Wet 이 8분 뒤 따라오는 최소 데이터.

    올림과 내림을 섞는 이유는 aligned 표의 부호 정렬이 실제로 일어나게
    하기 위해서다 — 한 방향만 있으면 그 열이 맞는지 알 수 없다.

    조정 뒤로 조용한 구간을 길게 둔다. noise_floor 가 모든 이벤트에서 60분
    떨어진 구간만 쓰므로, 짧게 만들면 σ 가 NaN 이 되어 판정이 흐려진다.
    """
    gap, wet = S.GAP_ITEM_IDS[0], S.WET_ITEM_IDS[0]
    rows = []
    for m in range(300):
        at = pd.Timestamp("2026-03-01 09:00") + pd.Timedelta(minutes=m)
        g = 300.0 + (3.0 if m >= 30 else 0.0) + (-3.0 if m >= 100 else 0.0)
        w = 18.20
        if m >= 38:
            w += 0.06 * (1 - np.exp(-(m - 38) / 10))
        if m >= 108:
            w -= 0.06 * (1 - np.exp(-(m - 108) / 10))
        rows.append(("L1", at, "P1", gap, g))
        rows.append(("L1", at, "P1", wet, round(w, 4)))
    df = pd.DataFrame(rows, columns=[S.LOT, S.AT, S.PRODUCT, S.ITEM, S.VALUE])
    df[S.ROW_NO] = range(len(df))
    return df.merge(parse.load_item_dictionary(), on=S.ITEM, how="left")


# ── dump.py — 파일을 쓰는 유일한 곳 ──────────────────────────────────────


def test_write_tables_creates_one_csv_per_table(tmp_path):
    paths = dump.write_tables(
        {"01_a": pd.DataFrame({"x": [1, 2]}), "02_b": pd.DataFrame({"y": [3]})},
        tmp_path,
    )
    assert {p.name for p in paths} >= {"01_a.csv", "02_b.csv"}
    assert (tmp_path / "01_a.csv").exists()
    assert (tmp_path / "02_b.csv").exists()


def test_empty_table_is_written_with_its_header(tmp_path):
    """0건과 '아예 안 돌았다' 는 다른 진단이다. 빈 표도 열 이름은 남겨야
    나중에 파일만 보고 그 둘을 가를 수 있다."""
    dump.write_tables({"02_events": pd.DataFrame(columns=["event_id", "at"])}, tmp_path)
    text = (tmp_path / "02_events.csv").read_text(encoding="utf-8-sig")
    assert text.strip() == "event_id,at"


def test_csv_starts_with_bom_so_excel_reads_hangul(tmp_path):
    """사내 PC 의 엑셀은 BOM 이 없으면 utf-8 을 cp949 로 읽어 한글을 깬다."""
    dump.write_tables({"01_a": pd.DataFrame({"사유": ["오염"]})}, tmp_path)
    assert (tmp_path / "01_a.csv").read_bytes().startswith(b"\xef\xbb\xbf")


def test_manifest_records_the_shape_of_every_table(tmp_path):
    dump.write_tables(
        {"01_a": pd.DataFrame({"x": [1, 2, 3]}), "02_b": pd.DataFrame(columns=["y"])},
        tmp_path,
    )
    text = (tmp_path / "_manifest.txt").read_text(encoding="utf-8")
    assert "01_a" in text and "3" in text
    assert "02_b" in text and "0" in text


def test_manifest_records_the_settings_that_shaped_this_dump(tmp_path):
    """폴더 둘을 비교할 때 무엇이 달라서 결과가 달라졌는지 알아야 한다."""
    dump.write_tables(
        {"01_a": pd.DataFrame({"x": [1]})},
        tmp_path,
        meta={"event_merge_minutes": 2, "input": "raw/실데이터.parquet"},
    )
    text = (tmp_path / "_manifest.txt").read_text(encoding="utf-8")
    assert "event_merge_minutes" in text and "2" in text
    assert "raw/실데이터.parquet" in text


def test_run_dir_is_named_by_the_clock(tmp_path):
    d = dump.new_run_dir(tmp_path, now=datetime(2026, 9, 1, 14, 30, 22))
    assert d.name == "20260901-143022"
    assert d.is_dir()


def test_two_runs_land_in_separate_directories(tmp_path):
    a = dump.new_run_dir(tmp_path, now=datetime(2026, 9, 1, 14, 30, 22))
    b = dump.new_run_dir(tmp_path, now=datetime(2026, 9, 1, 14, 31, 5))
    assert a != b


def test_same_second_reuses_the_directory_without_raising(tmp_path):
    """같은 초에 두 번 도는 것은 방어하지 않기로 했다. 다만 죽지는 않아야 한다."""
    now = datetime(2026, 9, 1, 14, 30, 22)
    assert dump.new_run_dir(tmp_path, now=now) == dump.new_run_dir(tmp_path, now=now)


# ── report — 계산은 그대로 두고 표만 건네준다 ───────────────────────────


def test_profile_readings_fills_the_requested_tables():
    tables = {}
    report.profile_readings(_readings_with_two_events(), tables=tables)
    assert set(tables) == set(report.DUMP_TABLES)


def test_profile_readings_writes_no_files_even_when_collecting(tmp_path, monkeypatch):
    """이 함수의 계약은 '파일을 만지지 않는다' 다. 표를 모아 주더라도 그건
    호출자에게 건네주는 것이지 쓰는 것이 아니다."""
    monkeypatch.chdir(tmp_path)
    tables = {}
    report.profile_readings(_readings_with_two_events(), tables=tables)
    assert list(tmp_path.iterdir()) == []


def test_collecting_tables_does_not_change_the_verdict():
    """덤프는 관찰이지 개입이 아니다. 켜고 끄는 것으로 결론이 달라지면
    덤프를 보고 판단할 수가 없다."""
    readings = _readings_with_two_events()
    plain = report.profile_readings(readings)
    collected = report.profile_readings(readings, tables={})
    for key in ("verdict", "n_events", "n_clean_events", "effective_rank"):
        assert plain[key] == collected[key]


def test_events_table_agrees_with_the_reported_count():
    tables = {}
    facts = report.profile_readings(_readings_with_two_events(), tables=tables)
    assert len(tables["02_events"]) == facts["n_events"]


def test_aligned_table_carries_the_sign_aligned_response():
    """05_aligned 는 부호 정렬 뒤의 값이어야 한다. 내린 조정(d_gap<0)까지
    포함해 늦은 lag 의 평균이 양수로 서는지로 확인한다."""
    tables = {}
    report.profile_readings(_readings_with_two_events(), tables=tables)
    aligned = tables["05_aligned"]
    assert (aligned["d_gap"] < 0).any(), "내린 조정이 표에 있어야 검증이 성립한다"
    late = aligned[aligned["lag_min"] >= 30]["response"]
    assert late.mean() > 0


# ── run() — 배선 ────────────────────────────────────────────────────────


def test_run_writes_no_dump_unless_asked(tmp_path):
    report.run(SAMPLE, DICT, out_dir=tmp_path)
    assert not (tmp_path / "dump").exists()


def test_run_with_dump_writes_every_table_and_a_manifest(tmp_path):
    report.run(SAMPLE, DICT, out_dir=tmp_path, dump=True)
    runs = list((tmp_path / "dump").iterdir())
    assert len(runs) == 1
    written = {p.name for p in runs[0].iterdir()}
    assert written == {f"{n}.csv" for n in report.DUMP_TABLES} | {"_manifest.txt"}


def test_dumped_csv_can_be_read_back_with_the_same_rows(tmp_path):
    """엑셀로 여는 것이 목적이지만, 깨진 CSV 는 엑셀에서도 안 열린다."""
    report.run(SAMPLE, DICT, out_dir=tmp_path, dump=True)
    run_dir = next((tmp_path / "dump").iterdir())
    for name in report.DUMP_TABLES:
        pd.read_csv(run_dir / f"{name}.csv")  # 파싱만 되면 된다


# ── 설정과 CLI ──────────────────────────────────────────────────────────


def test_cli_dump_defaults_to_none_so_run_stays_single_source():
    """--dump 를 안 주면 '지정 안 함' 이다. 켜고 끄는 기본값은 .env 가 정하고
    run() 한 곳에서만 읽는다 — 기존 --input·--out 과 같은 관례다."""
    assert report.build_parser().parse_args([]).dump is None
    assert report.build_parser().parse_args(["--dump"]).dump is True


def test_dump_is_off_by_default():
    """실데이터는 매 실행마다 수 MB 를 남긴다. 기본은 꺼져 있어야 한다."""
    assert get_settings().coating_dump_enabled is False
