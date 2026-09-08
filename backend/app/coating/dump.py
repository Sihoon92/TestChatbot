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
    # 표와 같은 ENCODING 을 쓴다. 이것도 사람이 메모장으로 여는 텍스트라,
    # BOM 이 없으면 표는 멀쩡한데 매니페스트만 깨지는 이상한 폴더가 된다.
    p.write_text("\n".join(lines) + "\n", encoding=ENCODING)
    return p


def write_text(path, text: str) -> Path:
    """진단 텍스트를 그대로 파일에 쓴다. 계산하지 않는다.

    CLI 가 stdout 으로만 내면 사용자는 셸 리다이렉션(`> out.txt`)을 쓸 수밖에
    없는데, 그 경로가 사내 PC 에서 한글을 깨뜨린다. PowerShell 은 외부 프로그램의
    출력을 파일에 담기 전에 [Console]::OutputEncoding(한글 Windows 기본 cp949)으로
    **한 번 해석한다.** 프로그램이 아무리 UTF-8 로 내보내도 그 자리에서 깨지고,
    깨진 글자가 그대로 저장돼 무엇으로 열어도 돌아오지 않는다. 우리가 직접 쓰면
    셸이 무엇이든 상관없어진다.

    이 모듈에 두는 이유는 경계다 - 이 기능에서 파일을 쓰는 곳은 여기 하나여야
    한다(모듈 docstring). diagnose 가 직접 열기 시작하면 그 약속이 깨진다.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding=ENCODING)
    return p
