"""기종이 다른 parquet 여러 개를 하나로 합친다 — 지연(L·τ) 추정 표본을 늘리기 위해서다.

왜 합치나. 깨끗한 조정 이벤트가 기종당 10건 수준이라 지연을 1분 단위로 가를
표본이 모자란다. dead_time 판정은 `평균 ÷ SEM` 비율이라 기종별 gain 차이에
거의 불변인 반면(섞어서 생기는 분산 팽창은 표본 30·gain 차 30% 기준 +1.2%),
이벤트가 2배가 되면 SEM 이 29% 줄어든다. 얻는 쪽이 압도적이다.

그래서 이 모듈이 지켜야 할 것은 "빠르게 붙이기" 가 아니라 **붙여도 되는
파일인지 먼저 따지는 것**이다. 여기서 걸러야 할 사고 둘은 전부 예외 없이
조용히 틀리는 종류다.

  lot id 충돌   — compress_runs 가 서로 다른 제품의 행을 한 lot 으로 보고
                  존재하지 않는 '변경' 을 만들어낸다. 이벤트가 늘어난 것처럼
                  보이는데 그 이벤트는 아무도 조정한 적이 없다.
  item_id 숫자형 — 사전 조인(문자열 키)이 통째로 미매칭된다. 예외는 안 나고
                  '데이터에 없는 제어 항목' 목록만 길어진다.

그리고 합친 뒤에도 **제품을 다시 가를 수 있어야 한다**. 라인 속도는 설비
고정값이라 L 은 기종이 달라도 같지만(그래서 지연은 합쳐서 잰다), gain 은 다르고
커널은 기종별 gain 이 같다고 강제한다. 영향행렬을 제품별로 보려면, 그리고 호기가
다른 파일이 섞였는지 알려면 product 열이 살아 있어야 한다. 요약이 제품별로 나눠
찍는 이유다.
"""
from pathlib import Path

import pandas as pd
import pytest

from app.coating import merge, parse, pivot, report
from app.coating import schemas as S

GAP1, GAP2 = S.GAP_ITEM_IDS[0], S.GAP_ITEM_IDS[1]
WET1 = S.WET_ITEM_IDS[0]


# ── 입력 만들기 ─────────────────────────────────────────────────────────

def _lot_frame(lot, product, gaps, wets, start="2026-02-01 09:00"):
    """분 단위 long 테이블 한 lot. gaps 가 도중에 바뀌면 그 지점이 조정 이벤트다."""
    t0 = pd.Timestamp(start)
    rows = []
    for m, (g, w) in enumerate(zip(gaps, wets)):
        at = t0 + pd.Timedelta(minutes=m)
        rows.append((lot, at, product, GAP1, float(g)))
        rows.append((lot, at, product, WET1, float(w)))
    return pd.DataFrame(rows, columns=[S.LOT, S.AT, S.PRODUCT, S.ITEM, S.VALUE])


def _write(tmp_path, name, frame) -> Path:
    p = tmp_path / name
    frame.to_parquet(p, index=False)
    return p


@pytest.fixture
def a48(tmp_path):
    """48X1 — 조정 1건(gap 200 → 210)."""
    return _write(tmp_path, "48X1.parquet", _lot_frame(
        "LA1", "BNB48X1", [200, 200, 210, 210, 210], [18.2, 18.2, 18.2, 18.4, 18.4],
    ))


@pytest.fixture
def b50(tmp_path):
    """50S1 — 조정 1건. lot 도 제품도 48X1 과 겹치지 않는다."""
    return _write(tmp_path, "50S1.parquet", _lot_frame(
        "LB1", "BNB50S1", [300, 300, 315, 315, 315], [17.0, 17.0, 17.0, 17.3, 17.3],
        start="2026-03-05 14:00",
    ))


# ── 핵심 계약 ───────────────────────────────────────────────────────────

def test_merged_file_holds_every_row_of_both_sources(a48, b50, tmp_path):
    """합치기의 최소 조건. 한 줄이라도 새로 생기거나 사라지면 그 뒤 판정이 전부 틀린다."""
    out = merge.merge_parquet([a48, b50], tmp_path / "merged.parquet")
    merged = pd.read_parquet(out)

    assert len(merged) == len(pd.read_parquet(a48)) + len(pd.read_parquet(b50))
    assert set(merged[S.LOT]) == {"LA1", "LB1"}


def test_product_survives_so_the_two_can_be_split_again(a48, b50, tmp_path):
    """지연은 합쳐서 재도 되지만 gain 은 기종마다 다르다. 영향행렬을 제품별로
    갈라 보려면, 그리고 호기가 다른 파일이 섞였는지 알려면 이 열이 유일한 단서다."""
    out = merge.merge_parquet([a48, b50], tmp_path / "merged.parquet")
    merged = pd.read_parquet(out)

    assert set(merged[S.PRODUCT]) == {"BNB48X1", "BNB50S1"}
    assert set(merged.loc[merged[S.PRODUCT] == "BNB48X1", S.LOT]) == {"LA1"}


def test_merged_file_goes_through_the_normal_read_path(a48, b50, tmp_path):
    """합친 것도 원본이다. 특수 분기 없이 parse.load_readings 가 읽어야 한다."""
    out = merge.merge_parquet([a48, b50], tmp_path / "merged.parquet")
    r = parse.load_readings(out, source="parquet")

    assert len(r) == 20
    assert parse.unknown_item_ids(r) == []
    assert S.ROW_NO in r.columns


def test_events_are_the_sum_of_the_sources_not_more(a48, b50, tmp_path):
    """합치기가 성공했다는 유일하게 의미 있는 증거. 이벤트가 1+1=2 여야 한다.

    3 이 나오면 lot 경계를 넘어 가짜 변경이 생긴 것이고, 1 이 나오면 한쪽이
    통째로 사라진 것이다. 둘 다 예외 없이 조용히 벌어진다."""
    out = merge.merge_parquet([a48, b50], tmp_path / "merged.parquet")

    def events(path):
        return report.profile_dataset(path, None, source="parquet")["n_events"]

    assert events(out) == 2
    assert events(a48) == 1
    assert events(b50) == 1


def test_row_order_inside_a_lot_is_preserved(tmp_path):
    """dedupe_minute 은 같은 (lot·분·item) 의 여러 기록 중 **파일에 적힌 순서로
    마지막** 것을 그 분의 상태로 삼는다(row_no). 합치면서 행 순서를 흔들면
    엉뚱한 값이 이긴다 - 값만 조용히 달라지고 행 수는 그대로다."""
    dup = pd.DataFrame(
        [
            ("LA1", pd.Timestamp("2026-02-01 09:00"), "BNB48X1", GAP1, 200.0),
            ("LA1", pd.Timestamp("2026-02-01 09:00"), "BNB48X1", GAP1, 209.0),  # 나중 = 진짜
        ],
        columns=[S.LOT, S.AT, S.PRODUCT, S.ITEM, S.VALUE],
    )
    a = _write(tmp_path, "a.parquet", dup)
    b = _write(tmp_path, "b.parquet", _lot_frame("LB1", "BNB50S1", [300], [17.0]))

    out = merge.merge_parquet([a, b], tmp_path / "merged.parquet")
    deduped = pivot.dedupe_minute(parse.load_readings(out, source="parquet"))
    kept = deduped.loc[
        (deduped[S.LOT] == "LA1") & (deduped[S.ITEM] == GAP1), S.VALUE
    ].tolist()

    assert kept == [209.0]


# ── 붙이면 안 되는 파일을 막는다 ────────────────────────────────────────

def test_lot_id_collision_is_refused(tmp_path):
    """같은 lot id 가 양쪽에 있으면 compress_runs 가 두 제품의 행을 한 lot 으로
    보고 아무도 조정한 적 없는 '변경' 을 만들어낸다. 조용히 틀리므로 막는다."""
    a = _write(tmp_path, "a.parquet", _lot_frame("L1", "BNB48X1", [200, 210], [18.2, 18.4]))
    b = _write(tmp_path, "b.parquet", _lot_frame("L1", "BNB50S1", [300, 315], [17.0, 17.3]))

    with pytest.raises(ValueError) as e:
        merge.merge_parquet([a, b], tmp_path / "merged.parquet")

    assert "L1" in str(e.value)          # 어느 lot 이 겹쳤는지 말한다
    assert not (tmp_path / "merged.parquet").exists()   # 반쪽 파일을 안 남긴다


def test_numeric_item_id_is_refused(tmp_path):
    """손으로 만든 parquet 은 item_id 가 int64 로 들어간다. 그러면 사전 조인이
    전부 미매칭되는데 예외가 안 나고 '데이터에 없는 제어 항목' 만 길어진다.
    합치는 시점이 이 사고를 잡을 수 있는 마지막 지점이다."""
    good = _lot_frame("LA1", "BNB48X1", [200, 210], [18.2, 18.4])
    bad = _lot_frame("LB1", "BNB50S1", [300, 315], [17.0, 17.3])
    bad[S.ITEM] = bad[S.ITEM].astype("int64")

    a = _write(tmp_path, "a.parquet", good)
    b = _write(tmp_path, "b.parquet", bad)

    with pytest.raises(ValueError) as e:
        merge.merge_parquet([a, b], tmp_path / "merged.parquet")

    assert "b.parquet" in str(e.value)   # 어느 파일이 문제인지 말한다
    assert S.ITEM in str(e.value)


def test_different_column_sets_are_refused(tmp_path):
    """컬럼 구성이 다르면 concat 이 없는 자리를 NaN 으로 채운다. 행 수는 맞고
    파일도 만들어지므로 아무도 눈치채지 못한 채 그 열이 통째로 비게 된다."""
    a = _lot_frame("LA1", "BNB48X1", [200, 210], [18.2, 18.4])
    b = _lot_frame("LB1", "BNB50S1", [300, 315], [17.0, 17.3])
    b["설비명"] = "A호기"

    with pytest.raises(ValueError) as e:
        merge.merge_parquet(
            [_write(tmp_path, "a.parquet", a), _write(tmp_path, "b.parquet", b)],
            tmp_path / "merged.parquet",
        )

    assert "설비명" in str(e.value)
    assert not (tmp_path / "merged.parquet").exists()


def test_overwriting_an_input_is_refused(a48, b50):
    """실패하면 원본이 사라진 채로 끝난다. convert 와 같은 관례다."""
    with pytest.raises(ValueError, match="같은 파일"):
        merge.merge_parquet([a48, b50], a48)


def test_a_single_input_is_refused(a48, tmp_path):
    """합칠 것이 하나뿐이면 합치는 게 아니다. 조용히 복사본을 만들면 그게
    원본인지 파생물인지 나중에 아무도 모른다."""
    with pytest.raises(ValueError, match="두 개"):
        merge.merge_parquet([a48], tmp_path / "merged.parquet")


# ── 막지는 않되 반드시 알린다 ───────────────────────────────────────────

def test_item_set_difference_is_warned_not_refused(tmp_path):
    """한쪽에만 있는 항목은 실패가 아니다 - 기간이 다르면 자연히 생긴다.
    다만 호기가 다른 파일을 섞은 것도 똑같이 이렇게 보이므로 반드시 알린다."""
    a = _lot_frame("LA1", "BNB48X1", [200, 210], [18.2, 18.4])
    b = _lot_frame("LB1", "BNB50S1", [300, 315], [17.0, 17.3])
    b = pd.concat([b, pd.DataFrame([
        ("LB1", pd.Timestamp("2026-03-05 14:00"), "BNB50S1", GAP2, 88.0),
    ], columns=b.columns)], ignore_index=True)

    out = merge.merge_parquet(
        [_write(tmp_path, "a.parquet", a), _write(tmp_path, "b.parquet", b)],
        tmp_path / "merged.parquet",
    )
    text = merge.summarize(pd.read_parquet(out), out, ["a.parquet", "b.parquet"])

    assert GAP2 in text
    assert "한쪽에만" in text


def test_single_product_is_warned(tmp_path):
    """제품이 한 종뿐이면 합칠 이유가 없거나 product 열이 안 채워진 것이다.
    후자면 합친 뒤 두 기종을 영영 못 가른다 - 지연 비교 자체가 불가능해진다."""
    a = _write(tmp_path, "a.parquet", _lot_frame("LA1", "BNB48X1", [200, 210], [18.2, 18.4]))
    b = _write(tmp_path, "b.parquet", _lot_frame("LB1", "BNB48X1", [300, 315], [17.0, 17.3]))

    out = merge.merge_parquet([a, b], tmp_path / "merged.parquet")
    text = merge.summarize(pd.read_parquet(out), out, ["a.parquet", "b.parquet"])

    assert "제품이 1종" in text


def test_summary_breaks_down_by_product(a48, b50, tmp_path):
    """합친 파일이 무엇으로 이뤄졌는지 한 화면에 보여야 한다. 이게 없으면
    나중에 '이 parquet 에 50S1 이 들어 있었나?' 를 파일을 열어봐야 안다."""
    out = merge.merge_parquet([a48, b50], tmp_path / "merged.parquet")
    text = merge.summarize(pd.read_parquet(out), out, [a48.name, b50.name])

    assert "BNB48X1" in text and "BNB50S1" in text
    assert "lot 1" in text                  # 제품별 lot 수
    assert "2026-02-01" in text             # 제품별 기간


# ── CLI ─────────────────────────────────────────────────────────────────

def test_cli_merges_and_prints_where_it_went(a48, b50, tmp_path, capsys):
    out = merge.main([
        "--input", str(a48), str(b50), "--out", str(tmp_path / "merged.parquet"),
    ])
    assert Path(out).exists()
    printed = capsys.readouterr().out
    assert "merged.parquet" in printed
    assert "행 20" in printed
    assert "BNB50S1" in printed


def test_cli_reports_missing_input(a48, tmp_path):
    with pytest.raises(SystemExit) as e:
        merge.main([
            "--input", str(a48), str(tmp_path / "없는파일.parquet"),
            "--out", str(tmp_path / "merged.parquet"),
        ])
    assert "없는파일.parquet" in str(e.value)


def test_cli_turns_validation_failure_into_a_sentence(tmp_path):
    """트레이스백을 그대로 던지면 원인 문장이 스택 밑에 묻힌다. report·convert 와 같은 관례."""
    a = _write(tmp_path, "a.parquet", _lot_frame("L1", "BNB48X1", [200, 210], [18.2, 18.4]))
    b = _write(tmp_path, "b.parquet", _lot_frame("L1", "BNB50S1", [300, 315], [17.0, 17.3]))

    with pytest.raises(SystemExit) as e:
        merge.main(["--input", str(a), str(b), "--out", str(tmp_path / "merged.parquet")])

    assert "L1" in str(e.value)


# ── 항목이 나뉘어 담긴 파일 합치기 (--mode items) ───────────────────────
#
# 같은 기종인데 MES 가 input 항목과 output 항목을 따로 뽑아 준 경우다. 기종
# 병합과 축이 정반대다: 여기서는 **lot 이 겹치는 것이 정상**이고(같은 lot 을
# 항목만 나눠 담았으므로), 대신 **항목이 겹치면 안 된다**.

OS_GAP = "10030271"


def _item_frame(lot, product, series, start="2026-02-01 09:00"):
    """항목 몇 개만 담은 long 테이블. series 는 {item_id: [분별 값]}."""
    t0 = pd.Timestamp(start)
    rows = []
    n = max(len(v) for v in series.values())
    for m in range(n):
        at = t0 + pd.Timedelta(minutes=m)
        for item, values in series.items():
            rows.append((lot, at, product, item, float(values[m])))
    return pd.DataFrame(rows, columns=[S.LOT, S.AT, S.PRODUCT, S.ITEM, S.VALUE])


@pytest.fixture
def a_in(tmp_path):
    """input 항목만 — gap 조정 1건(200 → 210)."""
    return _write(tmp_path, "A_input.parquet", _item_frame(
        "LA1", "BNB48X1",
        {GAP1: [200, 200, 210, 210, 210], OS_GAP: [40, 40, 40, 40, 40]},
    ))


@pytest.fixture
def a_out(tmp_path):
    """output 항목만 — 같은 lot, 같은 분, Wet 만."""
    return _write(tmp_path, "A_output.parquet", _item_frame(
        "LA1", "BNB48X1", {WET1: [18.2, 18.2, 18.2, 18.4, 18.4]},
    ))


def test_items_mode_accepts_the_same_lot_in_both_files(a_in, a_out, tmp_path):
    """기종 병합이라면 막았을 lot 중복이 여기서는 정상이다 - 같은 lot 을 항목만
    나눠 담은 파일이기 때문이다."""
    out = merge.merge_parquet(
        [a_in, a_out], tmp_path / "A_all.parquet", mode="items"
    )
    merged = pd.read_parquet(out)

    assert len(merged) == len(pd.read_parquet(a_in)) + len(pd.read_parquet(a_out))
    assert set(merged[S.LOT]) == {"LA1"}
    assert set(merged[S.ITEM]) == {GAP1, OS_GAP, WET1}


def test_items_mode_result_is_one_analysable_dataset(a_in, a_out, tmp_path):
    """합치는 목적. 따로 있을 때는 어느 쪽으로도 이벤트를 못 세지만, 합치면
    입력 변경과 그 출력이 한 파일에 있으므로 판정이 돈다."""
    out = merge.merge_parquet(
        [a_in, a_out], tmp_path / "A_all.parquet", mode="items"
    )
    facts = report.profile_dataset(out, None, source="parquet")

    assert facts["n_events"] == 1
    assert facts["valid_zones"] == [1]


def test_items_mode_refuses_conflicting_values_for_a_shared_item(tmp_path):
    """같은 항목이 양쪽에 있고 값이 다르면 dedupe_minute 이 뒤 파일 값을 집는다.
    행 수도 그대로고 예외도 안 나서 아무도 눈치채지 못한다."""
    a = _write(tmp_path, "a.parquet", _item_frame(
        "LA1", "BNB48X1", {GAP1: [200, 200], OS_GAP: [40, 40]}))
    b = _write(tmp_path, "b.parquet", _item_frame(
        "LA1", "BNB48X1", {WET1: [18.2, 18.2], OS_GAP: [55, 55]}))

    with pytest.raises(ValueError, match="값이 파일마다 다르다"):
        merge.merge_parquet([a, b], tmp_path / "m.parquet", mode="items")


def test_items_mode_keeps_first_file_when_a_shared_item_agrees(tmp_path):
    """겹치더라도 값이 같으면 무해하다 - dedupe 가 접어도 결과가 같다. 막지 않고
    중복 행만 걷어낸다."""
    a = _write(tmp_path, "a.parquet", _item_frame(
        "LA1", "BNB48X1", {GAP1: [200, 200], OS_GAP: [40, 40]}))
    b = _write(tmp_path, "b.parquet", _item_frame(
        "LA1", "BNB48X1", {WET1: [18.2, 18.2], OS_GAP: [40, 40]}))

    out = merge.merge_parquet([a, b], tmp_path / "m.parquet", mode="items")
    merged = pd.read_parquet(out)

    assert len(merged) == 6            # 8행 중 겹친 OS_GAP 2행이 빠진다
    assert len(merged[merged[S.ITEM] == OS_GAP]) == 2


def test_items_mode_refuses_product_mismatch_on_the_same_lot(tmp_path):
    """한 lot 에 두 제품이 섞이면 lot_bounds 가 first() 로 하나만 집어 조용히
    틀린다. '같은 기종' 이 이 모드의 전제다."""
    a = _write(tmp_path, "a.parquet", _item_frame(
        "LA1", "BNB48X1", {GAP1: [200, 200]}))
    b = _write(tmp_path, "b.parquet", _item_frame(
        "LA1", "BNB52X2", {WET1: [18.2, 18.2]}))

    with pytest.raises(ValueError, match="product 가 파일마다 다르다"):
        merge.merge_parquet([a, b], tmp_path / "m.parquet", mode="items")


def test_items_mode_warns_when_no_lot_is_shared(tmp_path, capsys):
    """lot 이 하나도 안 겹치면 잘못 짝지은 파일일 가능성이 높다. 다만 기간이
    어긋나게 추출됐을 수도 있어 막지는 않는다."""
    a = _write(tmp_path, "a.parquet", _item_frame(
        "LA1", "BNB48X1", {GAP1: [200, 200]}))
    b = _write(tmp_path, "b.parquet", _item_frame(
        "LA2", "BNB48X1", {WET1: [18.2, 18.2]}))

    out = merge.merge_parquet([a, b], tmp_path / "m.parquet", mode="items")
    log = merge.log_path_for(out).read_text(encoding="utf-8")

    assert "겹치는 lot 이 없다" in log


def test_products_mode_is_the_default_and_still_refuses_lot_collision(a48, tmp_path):
    """새 모드를 더해도 기존 호출은 그대로여야 한다."""
    dupe = _write(tmp_path, "dupe.parquet", _lot_frame(
        "LA1", "BNB50S1", [300, 300], [17.0, 17.0]))

    with pytest.raises(ValueError, match="lot id 가 겹친다"):
        merge.merge_parquet([a48, dupe], tmp_path / "m.parquet")


# ── 로그 ────────────────────────────────────────────────────────────────
#
# 합치기는 행을 지우기도 한다(값이 같은 겹침). 무엇을 왜 지웠는지 파일에
# 남지 않으면, 몇 달 뒤 이 parquet 의 행 수가 왜 두 원본의 합이 아닌지
# 아무도 설명할 수 없다.

def test_log_records_inputs_and_mode(a_in, a_out, tmp_path):
    out = merge.merge_parquet(
        [a_in, a_out], tmp_path / "A_all.parquet", mode="items"
    )
    log = merge.log_path_for(out).read_text(encoding="utf-8")

    assert "모드: items" in log
    assert "A_input.parquet" in log and "A_output.parquet" in log
    assert "행 10" in log          # a_in 은 2항목 × 5분


def test_log_names_every_check_that_ran(a_in, a_out, tmp_path):
    """어느 검사가 돌았는지가 남아야 한다. 통과한 검사도 적는다 - '검사하지
    않은 것' 과 '검사해서 통과한 것' 은 다른 사실이다."""
    out = merge.merge_parquet(
        [a_in, a_out], tmp_path / "A_all.parquet", mode="items"
    )
    log = merge.log_path_for(out).read_text(encoding="utf-8")

    assert "## 검사" in log
    assert "컬럼 구성" in log
    assert "item_id" in log
    assert "product" in log


def test_log_records_removed_duplicate_rows_with_the_item_and_count(tmp_path):
    """지운 행은 반드시 항목·건수와 함께 남긴다."""
    a = _write(tmp_path, "a.parquet", _item_frame(
        "LA1", "BNB48X1", {GAP1: [200, 200], OS_GAP: [40, 40]}))
    b = _write(tmp_path, "b.parquet", _item_frame(
        "LA1", "BNB48X1", {WET1: [18.2, 18.2], OS_GAP: [40, 40]}))

    out = merge.merge_parquet([a, b], tmp_path / "m.parquet", mode="items")
    log = merge.log_path_for(out).read_text(encoding="utf-8")

    assert "## 처리" in log
    assert OS_GAP in log
    assert "2행" in log
    assert "b.parquet" in log


def test_log_reconciles_input_and_output_row_counts(tmp_path):
    """정산이 맞지 않으면 합치기가 조용히 행을 잃은 것이다."""
    a = _write(tmp_path, "a.parquet", _item_frame(
        "LA1", "BNB48X1", {GAP1: [200, 200], OS_GAP: [40, 40]}))
    b = _write(tmp_path, "b.parquet", _item_frame(
        "LA1", "BNB48X1", {WET1: [18.2, 18.2], OS_GAP: [40, 40]}))

    out = merge.merge_parquet([a, b], tmp_path / "m.parquet", mode="items")
    log = merge.log_path_for(out).read_text(encoding="utf-8")

    assert "## 정산" in log
    assert "8행" in log and "2행" in log and "6행" in log


def test_log_is_written_for_products_mode_too(a48, b50, tmp_path):
    """축이 달라도 남는 기록은 같은 형식이어야 한다."""
    out = merge.merge_parquet([a48, b50], tmp_path / "merged.parquet")
    log = merge.log_path_for(out).read_text(encoding="utf-8")

    assert "모드: products" in log
    assert "제거한 행 없음" in log


def test_cli_accepts_mode_items(a_in, a_out, tmp_path, capsys):
    out_path = tmp_path / "A_all.parquet"
    merge.main([
        "--mode", "items",
        "--input", str(a_in), str(a_out),
        "--out", str(out_path),
    ])
    printed = capsys.readouterr().out

    assert out_path.exists()
    assert str(merge.log_path_for(out_path)) in printed
