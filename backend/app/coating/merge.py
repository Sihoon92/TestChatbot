"""parquet 여러 개를 하나로 합친다. 축이 둘이고 `--mode` 가 그것을 고른다.

    products — 기종이 다른 파일들. lot 이 다르고 항목은 같다.
    python -m app.coating.merge --input raw/48X1.parquet raw/50S1.parquet \\
                               --out raw/merged.parquet

    items — 같은 기종인데 MES 가 input 항목과 output 항목을 따로 뽑아 준 경우.
    python -m app.coating.merge --mode items \\
                               --input raw/A_input.parquet raw/A_output.parquet \\
                               --out raw/A_all.parquet

두 축은 막는 것이 정반대다. products 는 lot 이 겹치면 막고, items 는 lot 이
겹치는 것이 정상이며(같은 lot 을 항목만 나눠 담았으므로) 대신 **같은 항목의 값이
파일마다 다르면** 막는다. 어느 쪽이든 막는 이유는 하나다 - dedupe_minute 이
(lot·분·item) 중복을 파일 순서상 마지막 값으로 접기 때문에, 잘못 붙이면 한쪽이
조용히 이기고 행 수도 파일 크기도 그대로다.

합치기는 행을 지우기도 한다(items 에서 값이 같은 겹침). 그래서 무엇을 검사했고
무엇을 왜 지웠는지를 출력 옆 `<이름>.merge-log.txt` 에 남긴다 - 이 기록이 없으면
몇 달 뒤 이 parquet 의 행 수가 왜 두 원본의 합이 아닌지 아무도 설명할 수 없다.

── products 축 ─────────────────────────────────────────────────────────

왜 합치나. 지연(L·τ)을 1분 단위로 가르려면 표본이 필요한데 깨끗한 조정 이벤트가
기종당 10건 수준이다. dead_time 판정은 `평균 ÷ SEM` 비율이라 기종별 gain 차이에
거의 불변인 반면(분산 팽창은 표본 30·gain 차 30% 기준 +1.2%), 이벤트가 2배면
SEM 이 29% 줄어든다. 얻는 쪽이 압도적이다.

**영향행렬(gain)까지 이 파일로 학습하라는 뜻은 아니다.** 커널은 기종별 gain 이
같다는 가정을 강제하므로, 그건 제품별로 따로 보거나 gain 스케일만 제품별로 둔
형태여야 한다. 이 파일의 용도는 지연 추정이다.

그래서 이 모듈의 본체는 붙이는 것이 아니라 **붙여도 되는지 따지는 것**이다.
막는 것들은 전부 예외 없이 조용히 틀리는 종류다 - 파일은 만들어지고 행 수도
맞는데 결과만 틀린다.

합친 뒤에도 제품을 다시 가를 수 있어야 한다. 라인 속도는 설비 고정값이라
(COATING_LINE_SPEED_MPM) L = 거리/속도 는 기종이 달라도 같은 값이다 - 지연을
합쳐서 재는 근거가 여기 있다. 하지만 gain 은 그렇지 않고 커널은 기종별 gain 이
같다고 강제하므로, 영향행렬은 제품별로 갈라 봐야 한다. 호기가 다른 파일이
섞였는지 확인하는 것도 같은 열로 한다. product 열이 그 유일한 단서다.
"""
import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.coating import parse
from app.coating import schemas as S

# 합친 뒤 행 순서를 정하는 임시 열. 파일에는 남기지 않는다.
_ORDER = "_merge_order"
# 어느 입력에서 온 행인지. 지운 행을 로그에 적을 때 쓰고 파일에는 남기지 않는다.
_SRC = "_merge_src"

MODES = ("products", "items")
# 로그는 출력 옆에 같은 이름으로 둔다. 파일 하나만 옮겨도 기록이 따라오지 않는
# 것보다, 이름으로 짝이 보이는 편이 낫다.
LOG_SUFFIX = ".merge-log.txt"


def log_path_for(out_path) -> Path:
    """그 출력의 병합 로그 경로. raw/A_all.parquet -> raw/A_all.merge-log.txt"""
    out = Path(out_path)
    return out.with_name(out.stem + LOG_SUFFIX)


def merge_parquet(inputs, out_path, mode: str = "products") -> Path:
    """parquet 여러 개를 읽어 검증하고 하나로 쓴다. 쓴 경로를 돌려준다.

    검증을 전부 통과한 뒤에야 쓴다 - 반쪽 파일을 남기면 그것이 원본인지
    실패한 산출물인지 파일만 봐서는 알 수 없다.

    로그는 parquet 을 쓴 **뒤에** 쓴다. 검증에서 멈추면 로그도 남지 않아야
    한다 - 있지도 않은 파일을 설명하는 기록이 남으면 그게 더 헷갈린다.
    """
    paths = [Path(p) for p in inputs]
    out = Path(out_path)
    if mode not in MODES:
        raise ValueError(f"알 수 없는 모드: {mode!r} ({' | '.join(MODES)})")
    if len(paths) < 2:
        raise ValueError(
            f"합치려면 입력이 두 개 이상이어야 한다 (지금 {len(paths)}개).\n"
            "  하나뿐이면 합치는 것이 아니라 복사다 - 그 파일이 원본인지 파생물인지\n"
            "  나중에 구별할 수 없게 된다."
        )
    for p in paths:
        if out.resolve() == p.resolve():
            # convert.to_parquet 과 같은 관례. 실패하면 원본이 사라진 채로 끝난다.
            raise ValueError(f"입력과 출력이 같은 파일이다: {out}\n  --out 으로 다른 경로를 준다.")

    frames = [_read_one(p) for p in paths]
    _require_same_columns(paths, frames)
    checks = [
        f"[통과] 컬럼 구성 일치 ({len(frames[0].columns)}열)",
        "[통과] item_id 문자열 (사전 조인이 되는 타입)",
    ]
    if mode == "products":
        _require_disjoint_lots(paths, frames)
        n_lots = sum(f[S.LOT].nunique() for f in frames)
        checks.append(f"[통과] lot id 겹침 없음 (합계 {n_lots}개)")
    else:
        _require_consistent_product(paths, frames)
        checks.append("[통과] lot 별 product 일치 (같은 기종)")
        checks.append(_lot_overlap_line(frames))

    merged = pd.concat(
        [f.assign(**{_SRC: i}) for i, f in enumerate(frames)], ignore_index=True
    )
    n_input = len(merged)
    removed: list[tuple[str, int, str]] = []
    if mode == "items":
        shared = _shared_items(frames)
        checks.append(_shared_item_line(shared))
        if shared:
            _require_no_value_conflict(paths, merged, shared)
            merged, removed = _drop_agreeing_duplicates(paths, merged, shared)

    # 파일에 적힌 순서가 곧 "그 분의 나중 값" 을 정한다(pivot.dedupe_minute 의
    # row_no). 정렬하면서 그 순서를 잃으면 같은 분의 엉뚱한 값이 이기는데, 행 수도
    # 파일 크기도 그대로라 아무도 눈치채지 못한다. 그래서 원래 순서를 tie-break
    # 으로 명시한다 - lexsort 의 안정성에 기대지 않는다.
    merged[_ORDER] = range(len(merged))
    merged = (
        merged.sort_values([S.LOT, S.AT, _ORDER], kind="mergesort")
        .drop(columns=[_ORDER, _SRC])
        .reset_index(drop=True)
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(out, index=False)
    log_path_for(out).write_text(
        build_log(mode, paths, frames, out, checks, removed, n_input, len(merged)),
        encoding="utf-8",
    )
    return out


def _read_one(path: Path) -> pd.DataFrame:
    """읽고 정규화한다. 여기서만 item_id 의 타입을 따진다.

    normalize 는 시각·값만 손보고 item_id 는 건드리지 않는다. 손으로 만든
    parquet(`pd.read_csv` 를 dtype 없이 돌린 것)은 item_id 가 int64 로 들어가는데,
    그러면 사전 조인(문자열 키)이 통째로 미매칭되면서도 **예외가 안 난다** -
    '데이터에 없는 제어 항목' 목록만 길어지고 파이프라인은 끝까지 돈다.
    합치는 시점이 이 사고를 잡을 수 있는 마지막 지점이다.
    """
    frame = parse.normalize(parse.read_source(path, source="parquet"), path)
    if pd.api.types.is_numeric_dtype(frame[S.ITEM]):
        raise ValueError(
            f"{S.ITEM} 가 숫자형이다: {path} ({frame[S.ITEM].dtype})\n"
            "  사전은 문자열 키라 조인이 통째로 미매칭되는데 예외가 나지 않는다.\n"
            "  원본에서 다시 만든다: python -m app.coating.convert --input <원본>"
        )
    return frame


def _require_same_columns(paths, frames) -> None:
    """컬럼 구성이 다르면 concat 이 없는 자리를 NaN 으로 채운다. 행 수는 맞고
    파일도 만들어지므로, 그 열이 통째로 빈 것을 아무도 눈치채지 못한다."""
    base = list(frames[0].columns)
    for p, f in zip(paths[1:], frames[1:]):
        if set(f.columns) != set(base):
            only_here = sorted(set(f.columns) - set(base))
            only_there = sorted(set(base) - set(f.columns))
            raise ValueError(
                f"컬럼 구성이 다르다: {paths[0].name} 와 {p.name}\n"
                f"  {p.name} 에만: {only_here or '없음'}\n"
                f"  {paths[0].name} 에만: {only_there or '없음'}\n"
                "  같은 파이프라인(app.coating.convert)으로 만든 파일끼리 합친다."
            )


def _require_disjoint_lots(paths, frames) -> None:
    """lot id 가 겹치면 막는다.

    compress_runs 는 (lot, item) 으로 묶어 직전 값과 비교한다. 서로 다른 제품의
    행이 한 lot 에 들어가면 제품이 바뀌는 자리마다 '변경' 이 만들어지는데, 그건
    아무도 조정한 적이 없는 이벤트다. 이벤트가 늘어난 것처럼 보여서 판정이
    통과해버리는 것이 이 사고의 가장 나쁜 점이다.
    """
    seen: dict[str, Path] = {}
    for p, f in zip(paths, frames):
        for lot in f[S.LOT].unique():
            first = seen.get(lot)
            if first is not None:
                raise ValueError(
                    f"lot id 가 겹친다: {lot!r} 가 {first.name} 와 {p.name} 양쪽에 있다\n"
                    "  한 lot 에 두 제품의 행이 섞이면 제품이 바뀌는 자리마다\n"
                    "  아무도 조정한 적 없는 '변경' 이 만들어진다.\n"
                    "  추출 조건을 확인하거나 lot id 에 접두사를 붙여 구분한다."
                )
            seen[lot] = p


def _require_consistent_product(paths, frames) -> None:
    """한 lot 의 product 가 파일마다 다르면 막는다.

    items 축의 전제가 "같은 기종" 이다. 어긋나면 잘못 짝지은 파일인데, 합치고
    나면 lot_bounds 가 first() 로 둘 중 하나만 집어 조용히 틀린다 - 그 lot 의
    gain 이 통째로 엉뚱한 제품에 기록된다.
    """
    seen: dict[str, tuple[str, Path]] = {}
    for p, f in zip(paths, frames):
        for lot, product in f[[S.LOT, S.PRODUCT]].drop_duplicates().itertuples(index=False):
            first = seen.get(lot)
            if first is not None and first[0] != product:
                raise ValueError(
                    f"lot {lot!r} 의 product 가 파일마다 다르다\n"
                    f"  {first[1].name} -> {first[0]}\n"
                    f"  {p.name} -> {product}\n"
                    "  같은 기종의 항목 분할 파일이 아니다. 추출 조건을 확인한다."
                )
            seen.setdefault(lot, (product, p))


def _lot_overlap_line(frames) -> str:
    """lot 교집합. 없으면 경고한다 - 막지는 않는다.

    항목만 나눠 담은 파일이라면 lot 이 겹쳐야 정상이다. 하나도 안 겹치면 잘못
    짝지은 파일일 가능성이 높다. 다만 기간이 어긋나게 추출됐을 수도 있어서
    실패로 다루지는 않는다 - 그건 데이터를 보는 사람이 판단할 일이다.
    """
    common = set.intersection(*(set(f[S.LOT]) for f in frames))
    if common:
        return f"[통과] 겹치는 lot {len(common)}개 (항목 분할 파일의 정상 모습)"
    return (
        "[경고] 겹치는 lot 이 없다 — 항목만 나눈 파일이라면 lot 이 겹쳐야 한다.\n"
        "         기간이 어긋나게 추출됐는지, 짝이 맞는 파일인지 확인한다."
    )


def _shared_items(frames) -> list[str]:
    """두 개 이상의 파일에 다 있는 항목 ID."""
    seen: set[str] = set()
    shared: set[str] = set()
    for f in frames:
        items = set(f[S.ITEM])
        shared |= seen & items
        seen |= items
    return sorted(shared)


def _shared_item_line(shared) -> str:
    if not shared:
        return "[통과] 항목 겹침 없음 (파일마다 다른 항목을 담고 있다)"
    return (
        f"[경고] 양쪽 파일에 다 있는 항목 {len(shared)}개: "
        f"{shared[:10]}{' …' if len(shared) > 10 else ''} — 값은 일치"
    )


def _require_no_value_conflict(paths, merged: pd.DataFrame, shared) -> None:
    """겹치는 항목의 값이 (lot·분) 마다 같은지 본다. 다르면 막는다.

    같으면 무해하다 - dedupe_minute 이 접어도 결과가 같다. 다르면 뒤 파일 값이
    조용히 이긴다. 실측: OS Gap 이 양쪽에 40.0 과 55.0 으로 들어간 파일을 합치면
    dedupe 후 55.0 만 남는데, 행 수는 겹침이 없을 때와 똑같다.
    """
    sub = merged[merged[S.ITEM].isin(shared)]
    counts = sub.groupby([S.LOT, S.AT, S.ITEM])[S.VALUE].nunique(dropna=False)
    bad = counts[counts > 1]
    if bad.empty:
        return
    lot, at, item = bad.index[0]
    rows = sub[(sub[S.LOT] == lot) & (sub[S.AT] == at) & (sub[S.ITEM] == item)]
    where = "  ".join(
        f"{paths[int(r[_SRC])].name}={r[S.VALUE]}"
        for _, r in rows.drop_duplicates([_SRC]).iterrows()
    )
    more = f"  (외 {len(bad) - 1}건)" if len(bad) > 1 else ""
    raise ValueError(
        f"같은 항목의 값이 파일마다 다르다: {item!r}\n"
        f"  {lot} {at}  {where}{more}\n"
        "  합치면 뒤 파일 값이 조용히 이긴다(dedupe_minute 은 마지막 값을 집는다).\n"
        "  추출 조건이 겹쳤는지, 두 파일이 같은 설비의 것인지 확인한다."
    )


def _drop_agreeing_duplicates(paths, merged: pd.DataFrame, shared):
    """값이 같은 겹침 행을 걷어낸다. (남은 표, [(항목, 행수, 버린 파일)])

    앞 파일 것을 남긴다 - `--input` 에 적은 순서가 곧 우선순위라는 뜻이고,
    그래야 같은 명령을 두 번 돌렸을 때 같은 파일이 나온다.

    겹치지 않는 항목은 건드리지 않는다. 한 파일 안의 중복은 여기서 지울 것이
    아니다 - 그건 "그 분의 나중 값" 이라는 정보이고 dedupe_minute 의 몫이다.
    """
    dup = merged[S.ITEM].isin(shared) & merged.duplicated([S.LOT, S.AT, S.ITEM], keep="first")
    if not dup.any():
        return merged, []
    dropped = merged[dup]
    removed = [
        (item, len(g), ", ".join(sorted({paths[int(i)].name for i in g[_SRC]})))
        for item, g in dropped.groupby(S.ITEM, sort=True)
    ]
    return merged[~dup].reset_index(drop=True), removed


def build_log(mode, paths, frames, out, checks, removed, n_input, n_output) -> str:
    """무엇을 읽어 무엇을 검사하고 무엇을 지웠는지. ★순수 - 파일을 만지지 않는다.

    정산(입력 합계 − 제거 = 출력)을 마지막에 두는 이유는, 그 한 줄만 봐도
    행이 조용히 새지 않았는지 알 수 있어야 하기 때문이다.
    """
    lines = [
        "# 코팅 parquet 병합 로그",
        f"작성: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"모드: {mode}"
        + ("  (기종이 다른 파일 — lot 이 겹치면 안 된다)" if mode == "products"
           else "  (같은 기종, 항목이 나뉜 파일 — lot 이 겹치는 것이 정상이다)"),
        f"출력: {out}",
        "",
        "## 입력",
    ]
    for p, f in zip(paths, frames):
        lines.append(
            f"- {p.name}  행 {len(f):,} · 항목 {f[S.ITEM].nunique()}종"
            f" · lot {f[S.LOT].nunique()} · {f[S.AT].min()} ~ {f[S.AT].max()}"
        )
    lines += ["", "## 검사"]
    lines += [f"- {c}" for c in checks]
    lines += ["", "## 처리"]
    if removed:
        total = sum(n for _, n, _ in removed)
        lines.append(f"- 중복 행 제거 {total:,}행 (양쪽에 다 있고 값이 같은 항목)")
        for item, n, sources in removed:
            lines.append(f"    {item}  {n:,}행  ({sources} 쪽을 버렸다)")
        lines.append("  앞 파일 것을 남긴다 - --input 에 적은 순서가 우선순위다.")
    else:
        lines.append("- 제거한 행 없음")
    n_removed = n_input - n_output
    lines += [
        "",
        "## 정산",
        (f"- 입력 합계 {n_input:,}행 − 제거 {n_removed:,}행 = 출력 {n_output:,}행"
         if n_removed else f"- 입력 합계 {n_input:,}행 = 출력 {n_output:,}행"),
        "",
    ]
    return "\n".join(lines)


def summarize(merged: pd.DataFrame, out: Path, sources, mode="products") -> str:
    """무엇이 합쳐졌는지 적는다 - 내용을 모르는 파일을 남기지 않는다.

    제품별로 나눠 찍는 것이 핵심이다. 이게 없으면 몇 달 뒤 "이 parquet 에 50S1 이
    들어 있었나" 를 파일을 열어봐야 안다. 그리고 gain 을 제품별로 보려면 애초에
    무엇이 들어 있는지부터 알아야 한다.
    """
    size = Path(out).stat().st_size
    lines = [
        f"{out}  ({size / 1024 / 1024:.2f} MB)",
        f"  행 {len(merged):,} · lot {merged[S.LOT].nunique()}"
        f" · 항목 {merged[S.ITEM].nunique()}종"
        f" · 기간 {merged[S.AT].min()} ~ {merged[S.AT].max()}",
        f"  합친 파일: {', '.join(str(s) for s in sources)}",
        "  제품별:",
    ]
    for product, g in merged.groupby(S.PRODUCT, sort=True):
        lines.append(
            f"    {product}  lot {g[S.LOT].nunique()} · 행 {len(g):,}"
            f" · {g[S.AT].min()} ~ {g[S.AT].max()}"
        )
    # 제품 1종 경고는 products 축에서만 뜻이 있다. items 축은 "같은 기종" 이
    # 전제라 1종인 것이 정상이고, 거기서 이 문장을 찍으면 거짓말이 된다.
    if mode == "products" and merged[S.PRODUCT].nunique() < 2:
        lines.append(
            "  ! 제품이 1종뿐이다. 합칠 이유가 없거나 product 열이 안 채워진 것이다."
        )
        lines.append("    후자면 합친 뒤 두 기종을 다시 가를 수 없다 - gain 비교가 불가능해진다.")
    if mode == "products":
        lines += _item_gap_lines(merged)
    return "\n".join(lines)


def _item_gap_lines(merged: pd.DataFrame) -> list[str]:
    """제품 하나에만 있는 항목. 실패가 아니라 경고다.

    기간이 다르면 자연히 생긴다. 다만 **호기가 다른 파일을 섞은 것**도 똑같이
    이렇게 보이므로 반드시 눈에 띄어야 한다 - 호기가 다르면 gain 이 아니라 설비가
    다른 것이라 합칠 근거 자체가 사라진다.
    """
    by_product = {p: set(g[S.ITEM]) for p, g in merged.groupby(S.PRODUCT)}
    if len(by_product) < 2:
        return ["  한쪽에만 있는 항목: 없음"]
    shared = set.intersection(*by_product.values())
    odd = {p: sorted(items - shared) for p, items in by_product.items() if items - shared}
    if not odd:
        return ["  한쪽에만 있는 항목: 없음"]
    lines = [f"  ! 한쪽에만 있는 항목 {sum(len(v) for v in odd.values())}개:"]
    for product, items in sorted(odd.items()):
        lines.append(f"    {product} 에만: {items[:10]}{' …' if len(items) > 10 else ''}")
    lines.append("    기간 차이면 정상이다. 호기가 다른 파일을 섞었는지 확인한다.")
    return lines


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m app.coating.merge",
        description="parquet 을 하나로 합친다. 축은 --mode 가 고른다.",
        epilog=(
            "예) 기종이 다른 파일 합치기 (기본)\n"
            "    python -m app.coating.merge \\\n"
            "      --input data/coating/raw/48X1.parquet data/coating/raw/50S1.parquet \\\n"
            "      --out   data/coating/raw/merged.parquet\n\n"
            "예) 같은 기종인데 input·output 항목이 따로 있을 때\n"
            "    python -m app.coating.merge --mode items \\\n"
            "      --input data/coating/raw/A_input.parquet data/coating/raw/A_output.parquet \\\n"
            "      --out   data/coating/raw/A_all.parquet\n\n"
            "이후:  python -m app.coating.report --input <합친 parquet>"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--mode", choices=MODES, default="products",
        help="합치는 축. products(기본): 기종이 다른 파일 — lot 이 겹치면 막는다. "
             "items: 같은 기종인데 항목이 나뉜 파일 — lot 이 겹치는 것이 정상이고 "
             "같은 항목의 값이 어긋나면 막는다.",
    )
    p.add_argument(
        "--input", dest="input_paths", nargs="+", required=True, metavar="PATH",
        help="합칠 parquet 경로 두 개 이상",
    )
    p.add_argument(
        "--out", dest="out_path", required=True, metavar="PATH",
        help="출력 parquet 경로. 기본값을 두지 않는다 - 합친 파일은 원본이 아니므로 "
             "어디에 무슨 이름으로 남기는지 사람이 정해야 한다.",
    )
    return p


def main(argv: list[str] | None = None) -> str:
    args = build_parser().parse_args(argv)
    paths = [Path(p) for p in args.input_paths]
    missing = [p for p in paths if not p.exists()]
    if missing:
        raise SystemExit(
            "입력 파일을 찾을 수 없다:\n"
            + "\n".join(f"  {p}" for p in missing)
        )
    try:
        out = merge_parquet(paths, args.out_path, args.mode)
    except ValueError as e:
        # 원인은 이미 문장으로 만들어 뒀다. 트레이스백을 그대로 던지면 그 문장이
        # 스택 밑에 묻힌다(report·convert 와 같은 관례).
        raise SystemExit(str(e)) from e

    print(summarize(pd.read_parquet(out), out, [p.name for p in paths], args.mode))
    # 로그 경로를 찍는다 - 무엇을 지웠는지 궁금해진 시점에 파일을 찾아
    # 헤매지 않게 한다.
    print(f"  로그: {log_path_for(out)}")
    return str(out)


if __name__ == "__main__":
    main()
