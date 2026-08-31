"""원본(csv·xlsx)을 parquet 으로 한 번 바꿔, 그 뒤로는 그것을 원본으로 쓴다.

    python -m app.coating.convert --input raw/원본.xlsx --sheet 데이터
      -> raw/원본.parquet

왜 필요한가. xlsx 경로는 DRM 때문에 존재하는데 대가가 셋이다:
  1. 실행할 때마다 Excel 을 COM 으로 띄운다(실측 4.05s, 그중 ~2.8s 가 Excel).
  2. xlwings·pywin32 가 있어야 한다. requirements-coating.txt 가 그 줄에
     "COATING_INPUT_FORMAT=xlsx 일 때만 필요하다" 고 적어둔 그것이다. 사내
     폐쇄망에서는 받아야 할 패키지 수가 곧 설치 실패 확률이다.
  3. Excel 이 깔린 PC 에서만 데이터를 열 수 있다.

DRM 이 풀리는 PC 에서 **한 번** 변환해 두면 셋이 동시에 사라진다. 파일도 작아서
(실측 CSV 25.9MB -> parquet 0.6MB) 주고받기 쉽다.

무엇을 담는가. parse.normalize 까지다 - 표준 컬럼 이름과 타입만 맞춘 상태이고
사전(item_dictionary)은 담지 않는다. 사전을 담으면 사전을 고칠 때마다 파일을 다시
만들어야 하고, 그 순간 그 파일은 원본이 아니라 파생물이 된다.

표준 이름으로 바꿔 담는 것은 의도다. 헤더를 못 알아보는 파일이면 **배포하기 전
변환 시점에** 실패한다. 중국어·한국어 헤더 원본도 parquet 에는 worked_at 으로
담기니, 받아보는 사람이 별칭 표를 몰라도 된다.
"""
import argparse
from pathlib import Path

import pandas as pd

from app.coating import parse
from app.coating import schemas as S
from app.config import get_settings


def to_parquet(
    input_path,
    out_path=None,
    encodings=None,
    force_encoding=None,
    *,
    source="csv",
    sheet=None,
) -> Path:
    """원본을 읽어 정규화한 뒤 parquet 으로 쓴다. 쓴 경로를 돌려준다."""
    # 읽기 분기는 load_readings 와 공유한다. 복제하면 언젠가 갈라지고, 그때
    # 변환한 parquet 과 원본을 직접 읽은 결과가 조용히 어긋난다.
    raw = parse.read_source(
        input_path, encodings, force_encoding, source=source, sheet=sheet
    )
    normalized = parse.normalize(raw, input_path)
    out = Path(out_path) if out_path else default_out_path(input_path)
    if out.resolve() == Path(input_path).resolve():
        # 제자리 덮어쓰기는 막는다. 읽는 중에 쓰는 것도 문제지만, 실패하면 원본이
        # 사라진 채로 끝난다 - DRM 때문에 다시 만들기 어려운 파일이다.
        raise ValueError(f"입력과 출력이 같은 파일이다: {out}\n  --out 으로 다른 경로를 준다.")
    out.parent.mkdir(parents=True, exist_ok=True)
    normalized.to_parquet(out, index=False)
    return out


def default_out_path(input_path) -> Path:
    """같은 위치, 같은 이름, 확장자만 .parquet."""
    return Path(input_path).with_suffix(".parquet")


def summarize(normalized: pd.DataFrame, out: Path, src: Path, dict_path=None) -> str:
    """무엇을 담았는지 적는다 - 내용을 모르는 파일을 남기지 않는다.

    사전에 없는 항목은 실패가 아니라 경고다. 배포하기 전에 사전 갱신이 필요한지
    여기서 알게 하는 것이 목적이다.
    """
    size, src_size = out.stat().st_size, src.stat().st_size
    # 사전 조인은 파일에 담지 않지만, 조인이 되는지는 지금 확인해야 한다.
    joined = normalized.merge(
        parse.load_item_dictionary(dict_path), on=S.ITEM, how="left"
    )
    unknown = parse.unknown_item_ids(joined)
    lines = [
        f"{out}  ({size / 1024 / 1024:.2f} MB, 원본 대비 {size / src_size:.1%})",
        f"  행 {len(normalized):,} · lot {normalized[S.LOT].nunique()}"
        f" · 항목 {normalized[S.ITEM].nunique()}종"
        f" · 기간 {normalized[S.AT].min()} ~ {normalized[S.AT].max()}",
    ]
    if unknown:
        lines.append(
            f"  ! 사전에 없는 항목 {len(unknown)}개: {unknown[:10]}"
            f"{' …' if len(unknown) > 10 else ''}"
        )
        lines.append("    item_dictionary.csv 를 갱신하면 이 파일을 다시 안 만들어도 된다.")
    else:
        lines.append("  사전에 없는 항목: 없음")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """report.py 와 같은 관례: 기본값을 파서에 적지 않고 main() 한 곳에서 정한다."""
    p = argparse.ArgumentParser(
        prog="python -m app.coating.convert",
        description="원본(csv·xlsx)을 parquet 으로 바꾼다. 그 뒤로는 Excel 이 필요 없다.",
        epilog=(
            "예) python -m app.coating.convert --input data/coating/raw/원본.xlsx --sheet 데이터\n"
            "    -> data/coating/raw/원본.parquet\n"
            "이후:  python -m app.coating.report --input data/coating/raw/원본.parquet"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--input", "--csv", dest="input_path", default=None,
        help="원본 경로 (생략 시 COATING_INPUT_PATH)",
    )
    p.add_argument(
        "--out", dest="out_path", default=None,
        help="출력 parquet 경로 (생략 시 원본과 같은 위치·이름, 확장자만 .parquet)",
    )
    p.add_argument(
        "--format", choices=("csv", "xlsx", "parquet"), default=None,
        help="입력 형식. 생략하면 확장자로 판별하고, 모르는 확장자면 COATING_INPUT_FORMAT.",
    )
    p.add_argument(
        "--sheet", default=None,
        help="xlsx 에서 읽을 시트 (생략 시 COATING_XLSX_SHEET, 그것도 비면 첫 시트)",
    )
    p.add_argument(
        "--dict", dest="dict_path", default=None,
        help="항목 사전 CSV 경로 (생략 시 패키지에 든 스키마). 요약의 미등록 항목 확인에만 쓴다.",
    )
    p.add_argument(
        "--encoding", default=None,
        help="원본 CSV 인코딩을 하나로 강제한다 (생략 시 자동 판별 → COATING_CSV_ENCODINGS).",
    )
    return p


def main(argv: list[str] | None = None) -> str:
    args = build_parser().parse_args(argv)
    s = get_settings()
    in_path = (
        Path(args.input_path) if args.input_path
        else Path(s.resolved_coating_input_path)
    )
    if not in_path.exists():
        raise SystemExit(
            f"원본을 찾을 수 없다: {in_path}\n"
            "  --input 으로 경로를 지정하거나, COATING_INPUT_PATH 를 고친다."
        )
    source = parse.format_for(in_path, args.format, s.coating_input_format)
    try:
        out = to_parquet(
            in_path,
            args.out_path,
            s.coating_csv_encoding_list,
            args.encoding,
            source=source,
            sheet=args.sheet or s.coating_xlsx_sheet or None,
        )
    except ValueError as e:
        # parse·excel_source 가 원인을 이미 문장으로 만들어 뒀다. 트레이스백을
        # 그대로 던지면 그 문장이 스택 밑에 묻힌다.
        raise SystemExit(str(e)) from e

    print(summarize(pd.read_parquet(out), out, in_path, args.dict_path))
    return str(out)


if __name__ == "__main__":
    main()
