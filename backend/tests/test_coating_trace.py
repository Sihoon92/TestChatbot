"""전처리 로그 — 사람이 눈으로 검증하려면 각 줄이 손검증 가능해야 한다.

실데이터는 사내 PC 에만 있고 여기서는 볼 수 없다(CLAUDE.md). 그래서 이 표들의
계약을 합성으로 못박아 둔다 - 열이 조용히 바뀌면 사용자가 읽는 법을 잃는다.
"""
import pandas as pd
import pytest

from app.coating import events as ev_mod
from app.coating import schemas as S
from app.coating import trace

BASE = pd.Timestamp("2026-02-02 06:00")


def _changes(rows, lot="L1"):
    """(분, zone인덱스, prev, value) 목록으로 제어값 변경 표를 만든다."""
    return pd.DataFrame([
        {S.LOT: lot, S.ITEM: S.GAP_ITEM_IDS[zi], S.AT: BASE + pd.Timedelta(minutes=m),
         S.VALUE: v, S.PREV_VALUE: p}
        for m, zi, p, v in rows
    ])


def _bounds(lot="L1", start_min=-30, end_min=300):
    return pd.DataFrame([{
        S.LOT: lot,
        "start": BASE + pd.Timedelta(minutes=start_min),
        "end": BASE + pd.Timedelta(minutes=end_min),
    }])


def _pipeline(changes, pre=10, post=10, merge=2):
    ev, dl = ev_mod.build_events(changes, merge)
    iso = ev_mod.isolation(ev, pre, post, _bounds())
    return ev, dl, iso


def test_ledger_has_one_row_per_event_with_raw_timestamps():
    """원시 시각이 있어야 01_changes_control.csv 와 손으로 대조할 수 있다."""
    ch = _changes([(0, 0, 40.0, 41.0), (1, 1, 40.0, 41.0), (100, 2, 40.0, 41.0)])
    ev, dl, iso = _pipeline(ch)
    led = trace.event_ledger(iso, dl)
    assert len(led) == 2
    assert led.iloc[0][S.AT] == BASE
    assert led.iloc[0][S.LAST_AT] == BASE + pd.Timedelta(minutes=1)
    assert led.iloc[0]["zones"] == "z1,z2"


def test_ledger_blanks_run_id_when_the_run_has_one_event():
    """눈에 띄어야 할 것은 쪼개진 것뿐이다."""
    ch = _changes([(0, 0, 40.0, 41.0), (100, 1, 40.0, 41.0)])
    _, dl, iso = _pipeline(ch)
    led = trace.event_ledger(iso, dl)
    assert list(led[S.RUN]) == ["", ""]


def test_ledger_keeps_run_id_when_a_run_was_split():
    # 0→1.5→3 은 직전 변경과 매번 2분 이내(= 한 연쇄)지만 전체 폭이 3분이라
    # 앵커는 둘로 쪼갠다. run_id 가 그 둘을 다시 묶어 보여야 한다.
    ch = _changes([(0, 0, 40.0, 41.0), (1.5, 1, 40.0, 41.0), (3, 2, 40.0, 41.0),
                   (100, 3, 40.0, 41.0)])
    _, dl, iso = _pipeline(ch)
    led = trace.event_ledger(iso, dl).set_index(S.EVENT)
    split = led[led[S.RUN] != ""]
    assert len(split) == 2
    assert split[S.RUN].nunique() == 1


def test_ledger_flags_a_zone_touched_twice_in_one_event():
    """Δgap 이 눌릴 수 있는 자리다. 표시가 없으면 사람이 못 찾는다."""
    ch = _changes([(0, 0, 40.0, 41.0), (1, 0, 41.0, 42.0)])
    _, dl, iso = _pipeline(ch)
    led = trace.event_ledger(iso, dl)
    assert led.iloc[0]["n_dup_zones"] == 1


def test_ledger_carries_the_isolation_verdict_and_reason():
    ch = _changes([(0, 0, 40.0, 41.0), (5, 1, 40.0, 41.0), (100, 2, 40.0, 41.0)])
    _, dl, iso = _pipeline(ch)
    led = trace.event_ledger(iso, dl)
    assert list(led["isolated"]) == [False, False, True]
    assert led.iloc[0][S.ISO_REASON] == "뒤 5.0<10"


def test_funnel_accounts_for_every_drop():
    """단계마다 이름이 붙고, 증감이 앞 단계와 이어져야 한다."""
    ch = _changes([(0, 0, 40.0, 41.0), (5, 1, 40.0, 41.0), (100, 2, 40.0, 41.0)])
    ev, dl, iso = _pipeline(ch)
    readings = pd.DataFrame({S.LOT: ["L1"] * 50})
    deduped = pd.DataFrame({S.LOT: ["L1"] * 20})
    f = trace.funnel(readings, deduped, ch, iso)
    stages = list(f["stage"])
    assert stages[0] == "원본 행"
    assert f.iloc[0]["n"] == 50
    assert f.iloc[1]["n"] == 20
    assert f.iloc[1]["delta"] == -30
    # 마지막은 격리 통과
    assert stages[-1] == "격리 통과"
    assert f.iloc[-1]["n"] == 1


def test_funnel_names_the_rejection_breakdown():
    """탈락을 앞/뒤/양쪽으로 갈라야 창을 어느 쪽으로 움직일지 정해진다.

    "앞"·"뒤" 는 note 템플릿의 라벨이라 값이 0 이어도, 자리가 뒤바뀌어도
    항상 등장한다 - 글자 존재만 보면 숫자가 틀려도 통과한다. 그래서 값 자체를
    박는다. 0→5(뒤가 짧음)→100(앞이 짧음) 이므로 앞 1·뒤 1·양쪽 0 이어야 한다.
    """
    ch = _changes([(0, 0, 40.0, 41.0), (5, 1, 40.0, 41.0), (100, 2, 40.0, 41.0)])
    _, _, iso = _pipeline(ch)
    f = trace.funnel(pd.DataFrame({S.LOT: []}), pd.DataFrame({S.LOT: []}), ch, iso)
    note = f[f["stage"] == "격리 통과"].iloc[0]["note"]
    assert note == "탈락 앞 1 · 뒤 1 · 양쪽 0"


def test_funnel_rejection_buckets_partition_the_rejected_events():
    """세 갈래(앞/뒤/양쪽)의 합이 탈락 건수와 같아야 한다.

    사유 문자열이 세 접두어 중 어느 것과도 안 맞으면 그 이벤트는 note 집계에서
    조용히 사라진다 - 개수가 안 맞는데도 예외 없이 넘어간다. 이 등식이 그
    누락을 잡는 유일한 자리다. 0→3→8 이 연달아 짧아 뒤/양쪽/앞 세 사유를
    한 이벤트씩 만들고, 200 은 앞뒤 모두 넉넉해 격리를 통과한다:
      t=0  : gap_after =3  <10           -> "뒤 3.0<10"
      t=3  : gap_before=3, gap_after=5<10 -> "앞뒤 3.0/5.0<10/10"
      t=8  : gap_before=5  <10           -> "앞 5.0<10"
      t=200: 앞뒤 모두 충분               -> 격리
    """
    ch = _changes([(0, 0, 40.0, 41.0), (3, 1, 40.0, 41.0),
                   (8, 2, 40.0, 41.0), (200, 3, 40.0, 41.0)])
    _, _, iso = _pipeline(ch)

    ok = iso["isolated"].astype(bool)
    reasons = iso.loc[~ok, S.ISO_REASON]
    n_before = int(reasons.str.startswith("앞 ").sum())
    n_after = int(reasons.str.startswith("뒤 ").sum())
    n_both = int(reasons.str.startswith("앞뒤").sum())
    assert (n_before, n_after, n_both) == (1, 1, 1)
    assert n_before + n_after + n_both == len(iso) - int(ok.sum())

    f = trace.funnel(pd.DataFrame({S.LOT: []}), pd.DataFrame({S.LOT: []}), ch, iso)
    note = f[f["stage"] == "격리 통과"].iloc[0]["note"]
    assert note == "탈락 앞 1 · 뒤 1 · 양쪽 1"


def test_funnel_delta_is_nullable_int_not_float_nan():
    """delta 없음은 pd.NA 여야 한다 — float NaN 이 아니다.

    int 와 None 을 그냥 리스트로 섞어 DataFrame 열에 담으면 pandas 가 조용히
    float64 로 승격시켜 None 을 NaN 으로 바꾼다. 그 상태에서 소비자가 관례대로
    `is None` 으로 걸러내면 절대 걸리지 않는다 - diagnose._funnel_lines 가
    실제로 이렇게 당해 매 실행 '(+nan)' 을 냈다(Task 10). dtype 을 Int64 로
    명시해 두면 '델타 없음' 이 pd.NA 로 정직하게 남고, pd.isna() 로 float
    승격 없이도 잡힌다 - 이 계약이 깨지면 다음 소비자도 같은 함정을 밟는다.
    """
    ch = _changes([(0, 0, 40.0, 41.0), (5, 1, 40.0, 41.0), (100, 2, 40.0, 41.0)])
    _, _, iso = _pipeline(ch)
    f = trace.funnel(pd.DataFrame({S.LOT: []}), pd.DataFrame({S.LOT: []}), ch, iso)
    assert f["delta"].dtype == "Int64"

    # "앵커 묶음" 은 단위가 행에서 묶음으로 바뀌는 줄이라 뺄셈이 뜻을 잃는다.
    unit_change = f[f["stage"] == "앵커 묶음"].iloc[0]["delta"]
    assert pd.isna(unit_change)

    # "분 중복 접기" 는 앞 줄과 같은 단위(행)라 실제 정수 델타가 나와야 한다.
    real_delta = f[f["stage"] == "분 중복 접기"].iloc[0]["delta"]
    assert not pd.isna(real_delta)
    assert real_delta == 0

    # 실제 증감이 있는 줄에서도 부동소수점 오염(예: 3.0) 없이 정수로 나온다.
    changed = f[f["stage"] == "값이 바뀐 시점만"].iloc[0]["delta"]
    assert changed == len(ch)  # deduped 가 빈 프레임이라 0 에서 len(ch) 만큼 는다
    assert int(changed) == changed


def test_funnel_counts_fragments_of_continuous_runs():
    # 0→1.5→3 은 한 연쇄인데 앵커가 둘로 쪼갠다. 그 둘이 "조각" 이다.
    ch = _changes([(0, 0, 40.0, 41.0), (1.5, 1, 40.0, 41.0), (3, 2, 40.0, 41.0),
                   (100, 3, 40.0, 41.0)])
    _, _, iso = _pipeline(ch)
    f = trace.funnel(pd.DataFrame({S.LOT: []}), pd.DataFrame({S.LOT: []}), ch, iso)
    row = f[f["stage"] == "연속 조작 구간 조각"].iloc[0]
    assert row["n"] == 2


def test_legacy_events_fold_fragments_back_into_runs():
    """구 규칙의 묶음 = 연쇄 = run. 접으면 그대로 나온다."""
    ch = _changes([(0, 0, 40.0, 41.0), (1.5, 1, 40.0, 41.0), (3, 2, 40.0, 41.0),
                   (100, 3, 40.0, 41.0)])
    _, dl, iso = _pipeline(ch)
    old = trace.legacy_events(iso)
    assert len(old) == 2                      # 앞 셋이 한 연쇄
    assert S.LAST_AT not in old.columns       # 시작 기준으로 재게 한다


def test_rule_comparison_finds_events_only_the_old_rule_kept():
    """연쇄가 삼켰던 긴 구간이 여기서 사례로 나온다."""
    ch = _changes([(0, 0, 40.0, 41.0), (1.5, 1, 40.0, 41.0), (3, 2, 40.0, 41.0),
                   (4.5, 3, 40.0, 41.0), (200, 4, 40.0, 41.0)])
    _, dl, iso = _pipeline(ch)
    cmp = trace.rule_comparison(iso, dl, _bounds(), 30, 60, 10, 10)
    # 연쇄로 보면 0~4.5 가 한 덩어리 + 200 = 2. 앵커로 보면 {0,1.5} {3,4.5} {200} = 3.
    assert cmp["old"]["n_clusters"] == 2
    assert cmp["new"]["n_clusters"] == 3
    assert cmp["only_old"] >= 1
    assert cmp["only_old_examples"]
    ex = cmp["only_old_examples"][0]
    assert ex["n_fragments"] >= 2


def test_rule_comparison_records_windows_and_item_touch_counts():
    """pre/post 는 기록만 하고(호출부가 iso 를 이미 그 창으로 만들어 왔다),
    old/new 의 n_items 와 예시의 n_item_touches 는 서로 다른 것을 센다.

    old_pre=30/old_post=60, new_pre=10/new_post=10 을 손으로 되짚는다:
      run1={0,1.5,3,4.5}, run2={200} (연쇄 기준). legacy_events 로 접으면
      run1.worked_at=0, run2.worked_at=200 뿐이라, old_pre=30/old_post=60
      기준 간격은 run1: 앞 0-(-30)=30, 뒤 200-0=200 -> 격리.
      run2: 앞 200-0=200, 뒤 300-200=100 -> 격리. 즉 old 는 둘 다 산다
      (n_isolated=2) -> old 의 zoned delta 전부(5개 zone 각 1항목)가
      old["n_items"] 에 잡힌다 = 5.

      앵커 이벤트는 e1={0,1.5}, e2={3,4.5}, e3={200}. pre=10/post=10 로
      보면 e1↔e2 간격(gap_before(e2)=3-1.5=1.5)이 모자라 둘 다 탈락하고
      e3 만 격리를 통과한다 -> new["n_isolated"]=1, e3 의 zone(1개)만
      new["n_items"] 에 잡힌다 = 1.

      only_old_examples 의 run1 은 e1(2항목)+e2(2항목) 의 fragment 별
      nunique 를 그대로 더해 n_item_touches=4 다(실제 고유 zone 은 4개라
      우연히 항목 수와 같지만, 이름이 다른 것을 잰다는 사실은 바뀌지
      않는다 - 같은 zone 이 두 fragment 에 걸쳐 손질됐다면 여기서만
      더 커진다).
    """
    ch = _changes([(0, 0, 40.0, 41.0), (1.5, 1, 40.0, 41.0), (3, 2, 40.0, 41.0),
                   (4.5, 3, 40.0, 41.0), (200, 4, 40.0, 41.0)])
    _, dl, iso = _pipeline(ch)
    cmp = trace.rule_comparison(iso, dl, _bounds(), 30, 60, 10, 10)

    assert cmp["old"] == {"n_clusters": 2, "n_isolated": 2, "n_items": 5,
                           "pre": 30, "post": 60}
    assert cmp["new"] == {"n_clusters": 3, "n_isolated": 1, "n_items": 1,
                           "pre": 10, "post": 10}
    ex = cmp["only_old_examples"][0]
    assert ex["n_item_touches"] == 4
    assert "n_items" not in ex


def test_rule_comparison_counts_overlap_at_the_run_level():
    """구는 run, 신은 이벤트라 단위가 다르다. 겹침은 run 으로 센다."""
    ch = _changes([(0, 0, 40.0, 41.0), (200, 1, 40.0, 41.0)])
    _, dl, iso = _pipeline(ch, pre=10, post=10)
    cmp = trace.rule_comparison(iso, dl, _bounds(), 10, 10, 10, 10)
    assert cmp["both"] + cmp["only_new"] + cmp["only_old"] >= 1


def test_timeline_uses_one_common_scale_for_every_lot():
    """lot 마다 폭에 맞춰 늘이면 모든 lot 이 같은 길이로 보여 길이가 사라진다."""
    ch = pd.concat([
        _changes([(0, 0, 40.0, 41.0)], lot="L1"),
        _changes([(0, 1, 40.0, 41.0)], lot="L2"),
    ], ignore_index=True)
    ev, dl = ev_mod.build_events(ch, 2)
    bounds = pd.DataFrame([
        {S.LOT: "L1", "start": BASE, "end": BASE + pd.Timedelta(minutes=400)},
        {S.LOT: "L2", "start": BASE, "end": BASE + pd.Timedelta(minutes=100)},
    ])
    iso = ev_mod.isolation(ev, 10, 10, bounds)
    mpc, rows = trace.timeline(iso, bounds, width=40)
    by = {r[S.LOT]: r for r in rows}
    # 4배 긴 lot 이 4배 길게 그려져야 한다
    assert len(by["L1"]["cells"]) > 3 * len(by["L2"]["cells"])
    assert mpc == pytest.approx(10.0)


def test_timeline_includes_lots_with_no_isolated_event():
    """통과가 0건인 lot 이야말로 봐야 할 것이다."""
    ch = _changes([(0, 0, 40.0, 41.0), (5, 1, 40.0, 41.0)])
    _, _, iso = _pipeline(ch)
    _, rows = trace.timeline(iso, _bounds(), width=40)
    assert len(rows) == 1
    assert "✗" in rows[0]["cells"]


def test_timeline_marks_split_runs():
    # 0→1.5→3 은 한 연쇄인데 앵커가 둘로 쪼갠다.
    ch = _changes([(0, 0, 40.0, 41.0), (1.5, 1, 40.0, 41.0), (3, 2, 40.0, 41.0),
                   (200, 3, 40.0, 41.0)])
    _, _, iso = _pipeline(ch)
    _, rows = trace.timeline(iso, _bounds(), width=40)
    assert rows[0]["runs"]
    assert rows[0]["runs"][0]["n"] == 2


def test_timeline_cell_collision_always_shows_the_rejected_mark():
    """한 칸에 통과와 탈락이 겹치면 탈락(✗)이 남아야 한다 - 그리는 순서와 무관하게.

    한 lot 안에 두 그룹을 둔다. 앞쪽 그룹(칸5)은 통과(e1)가 먼저 그려지고
    탈락(e2·e3)이 나중에 겹쳐 덮어써야 하고, 뒤쪽 그룹(칸20)은 탈락(e4)이
    먼저 그려지고 통과(e5)가 나중에 겹쳐도 덮이면 안 된다. 두 순서 모두
    ✗ 로 끝나야 우선순위 규칙이 이벤트가 그려지는 순서(=시각 순서)에
    의존하지 않는다는 것이 보인다 - 어느 한쪽만 확인하면 "지금 이 순서에서만
    맞는" 우연일 수 있다.

    분/칸을 실행 전에 손으로 정한다: bounds span=4000분, width=40
    -> mpc=max(4000/40,1.0)=100. 칸 인덱스는 (AT-start)//100:
      e1(500)·e2(550)·e3(555) -> 전부 칸5 (500~599 구간).
      e4(2000)·e5(2050)       -> 전부 칸20 (2000~2099 구간).

    격리(pre=post=10)도 실행 전에 손으로 정한다(간격은 직전 이벤트의
    끝에서 잰다):
      e1: 앞=500-0(경계)=500, 뒤=550-500=50            -> 통과(✓)
      e2: 앞=550-500=50, 뒤=555-550=5<10               -> 탈락(✗, 뒤가 짧음)
      e3: 앞=555-550=5<10                              -> 탈락(✗, 앞이 짧음)
      e0: 이웃일 뿐 - e4 를 탈락시키려고 놓은 헬퍼. 자기 판정은 안 쓴다.
      e4: 앞=2000-1993(e0)=7<10                        -> 탈락(✗, 앞이 짧음)
      e5: 앞=2050-2000=50, 뒤=4000(경계)-2050=1950       -> 통과(✓)

    실행해서 확인한 값(코드 실행 결과, 손 계산과 일치): mpc=100.0,
    cells[5]='✗', cells[20]='✗'.
    """
    ch = _changes([
        (500, 0, 40.0, 41.0),
        (550, 1, 40.0, 41.0),
        (555, 2, 40.0, 41.0),
        (1993, 3, 40.0, 41.0),
        (2000, 4, 40.0, 41.0),
        (2050, 5, 40.0, 41.0),
    ])
    ev, _ = ev_mod.build_events(ch, 2)
    bounds = pd.DataFrame([{
        S.LOT: "L1", "start": BASE, "end": BASE + pd.Timedelta(minutes=4000),
    }])
    iso = ev_mod.isolation(ev, 10, 10, bounds)
    mpc, rows = trace.timeline(iso, bounds, width=40)
    assert mpc == pytest.approx(100.0)
    cells = rows[0]["cells"]
    # 칸5: ✓(e1)가 먼저 그려지고 ✗(e2,e3)가 나중에 덮는다.
    assert cells[5] == "✗"
    # 칸20: ✗(e4)가 먼저 그려지고 ✓(e5)가 나중에 와도 덮이지 않는다.
    assert cells[20] == "✗"


def test_timeline_clamps_an_event_exactly_at_bounds_end():
    """lot 끝 시각과 정확히 같은 이벤트도 문자열 밖(n 번째)이 아니라
    마지막 칸(n-1)에 들어가야 한다.

    bounds span=100분, width=10 -> mpc=max(100/10,1.0)=10, n=max(round(100/10),1)=10
    (칸 인덱스 0..9). 이벤트가 정확히 end 에 있으면 원시 인덱스는
    (100-0)/10=10.0 -> int 10 인데, 이는 유효 범위(0..9) 밖이다. 클램프
    (min(10, n-1=9))가 없으면 `cells[10] = ...` 에서 IndexError 가 난다.
    클램프가 있으면 9번(마지막) 칸에 들어간다.

    이 이벤트는 이웃이 없어 gap_before 는 경계(=100, 통과)지만 gap_after 는
    lot 끝과 자기 자신이 같아 0<10 으로 탈락한다 - 그래서 마지막 칸은 '✗'.
    실행해서 확인한 값(코드 실행 결과, 손 계산과 일치):
    cells == '─'*9 + '✗'.
    """
    ch = _changes([(100, 0, 40.0, 41.0)])
    ev, _ = ev_mod.build_events(ch, 2)
    bounds = pd.DataFrame([{
        S.LOT: "L1", "start": BASE, "end": BASE + pd.Timedelta(minutes=100),
    }])
    iso = ev_mod.isolation(ev, 10, 10, bounds)
    mpc, rows = trace.timeline(iso, bounds, width=10)
    assert mpc == pytest.approx(10.0)
    assert rows[0]["cells"] == "─" * 9 + "✗"
