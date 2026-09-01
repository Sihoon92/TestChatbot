"""중간 산출물을 CSV 로 남긴다. ★이 기능에서 파일을 쓰는 유일한 곳.

**검사용이지 캐시가 아니다.** 쓰기만 하고 아무도 읽지 않는다. 그래서 스탬프도
무효화 로직도 없다 - 걷어낸 interim 캐시(e287ef8 → 8ac3ae0)가 가장 믿기 어려웠던
부분이 바로 그 무효화였는데, 여기는 그 질문 자체가 없다. 파이프라인이 실제로
무엇을 보고 그런 판정을 냈는지 사람이 열어 확인하는 것이 유일한 목적이다.

CSV 를 고른 이유도 그 목적 하나다. parquet 이 이 패키지의 관례지만 사내 PC 에서
바로 못 연다. BOM 을 붙이는 것도 같은 이유다 - 없으면 엑셀이 utf-8 을 cp949 로
읽어 한글 사유("오염"·"정착")가 깨진다.

계산은 하지 않는다. 받은 표를 그대로 적을 뿐이라, 덤프를 켠 실행과 끈 실행의
결론이 달라질 여지가 없다.
"""
from datetime import datetime
from pathlib import Path

import pandas as pd

RUN_DIR_FORMAT = "%Y%m%d-%H%M%S"
MANIFEST = "_manifest.txt"
# 엑셀이 알아보는 utf-8. 모듈 docstring 참고.
ENCODING = "utf-8-sig"


def new_run_dir(root, now: datetime | None = None) -> Path:
    """이번 실행이 쓸 폴더. 없으면 만든다.

    같은 초에 두 번 돌면 같은 폴더를 다시 준다. 리포트가 1초보다 오래 걸려
    실질적으로 겹치지 않고, 겹치더라도 덮어쓰는 편이 실패보다 낫다 - 덤프는
    부산물이라 이것 때문에 리포트가 죽으면 주객이 전도된다.
    """
    d = Path(root) / (now or datetime.now()).strftime(RUN_DIR_FORMAT)
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_tables(
    tables: dict[str, pd.DataFrame], out_dir, meta: dict | None = None
) -> list[Path]:
    """표마다 CSV 하나, 그리고 매니페스트 하나.

    빈 표도 헤더를 남긴다. 0건인 것과 아예 안 돈 것은 다른 진단인데, 파일이
    없으면 나중에 폴더만 보고 그 둘을 가를 수가 없다.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = [
        _write_one(out, name, tables[name]) for name in sorted(tables)
    ]
    paths.append(_write_manifest(out, tables, meta or {}))
    return paths


def _write_one(out: Path, name: str, df: pd.DataFrame) -> Path:
    p = out / f"{name}.csv"
    df.to_csv(p, index=False, encoding=ENCODING)
    return p


def _write_manifest(out: Path, tables: dict, meta: dict) -> Path:
    """무엇이 얼마나 나왔는지 + 그것을 만든 설정.

    설정을 함께 적는 이유는 폴더 둘을 비교할 때다. 행 수만 다르고 왜 다른지
    모르면 비교가 추측이 된다.
    """
    lines = [
        "# 코팅 파이프라인 중간 산출물",
        f"작성: {datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        "## 표",
    ]
    for name in sorted(tables):
        df = tables[name]
        lines.append(f"- {name}: {len(df)}행 × {len(df.columns)}열")
    if meta:
        lines += ["", "## 이 덤프를 만든 입력과 설정"]
        lines += [f"- {k}: {v}" for k, v in meta.items()]
    p = out / MANIFEST
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p
