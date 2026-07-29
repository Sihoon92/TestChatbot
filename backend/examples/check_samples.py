"""샘플 엑셀에서 **LLM 없이** 기대값을 계산해 보여준다.

실행 (backend/ 에서):
    .venv/Scripts/python.exe examples/check_samples.py

## 왜 이게 필요한가

수집이 이상하게 나왔을 때 원인은 둘 중 하나다.

  (A) 파이프라인 로직이 틀렸다        — 조인·날짜·합산 규칙의 문제
  (B) 에이전트가 시트를 잘못 읽었다   — 레이아웃 발견의 문제

둘은 고치는 곳이 완전히 다른데, 수집 결과만 보면 구분이 안 된다. 이 스크립트는
엑셀을 **정해진 열 위치로 직접** 읽어(에이전트를 안 쓴다) 정답을 계산한다.
여기 나온 값과 실제 수집 결과가 다르면 (B), 여기부터 이상하면 (A) 다.

읽는 열 위치는 make_sample_uploads.py 가 쓴 그대로다. 실물 파일에는 쓸 수 없다.
"""
import math
import sys
from datetime import timedelta
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings           # noqa: E402
from app.excel.workbook import open_workbook   # noqa: E402

PPM = 1_000_000

# 격자는 top_left=B2 이므로 인덱스 0 = 열 B.
M_LINE, M_JIG_ID, M_CODE, M_EQUIP = 2, 5, 8, 9        # 기준정보
L_TIME, L_LOC, L_EQUIP = 0, 3, 5                       # 관리대장
X_CODE, X_IN, X_BAD = 3, 5, 7                          # MES (6행부터)
Q_MOLD = 2          # IQC 대장 상세표(33행~)의 '금형 번호' = 열 D
Q_HIST_MOLD = 4     # IQC 이력표(17행~)의 '관리 번호'      = 열 F


def _grid(path: Path, sheet: str):
    with open_workbook(str(path)) as wb:
        return wb.used_values(sheet)[0]


def main() -> int:
    root = Path(get_settings().resolved_ingest_root)

    # ── 기준정보: 설비명 → 금형 ──────────────────────────────────────
    master = {}
    for r in _grid(root / "JIG기준정보" / "JIG_기준정보.xlsx", "기준정보")[1:]:
        if r and r[M_EQUIP]:
            master[r[M_EQUIP]] = {
                "mold": str(r[M_JIG_ID]).lstrip("#"),
                "code": int(r[M_CODE]), "line": r[M_LINE],
            }

    # ── MES: (날짜, 설비코드) → (투입, 불량) ─────────────────────────
    mes = {}
    have_days = set()
    for path in sorted((root / "MES").glob("*.xlsx")):
        day = path.name.split("_")[0]
        have_days.add(day)
        for r in _grid(path, "불량현황")[4:]:
            if r and r[X_CODE]:
                mes[(day, int(r[X_CODE]))] = (int(r[X_IN]), int(r[X_BAD]))

    # ── 관리대장: 시트마다 사용구간 ──────────────────────────────────
    ledger = root / "EES" / "JIG_관리대장.xlsx"
    with open_workbook(str(ledger)) as wb:
        sheets = wb.sheet_names()
        grids = {s: wb.used_values(s)[0] for s in sheets}

    molds, unknown_equipment, missing_days, open_runs, unmatched = {}, [], set(), 0, 0

    for sheet in sheets:
        rows = sorted([r for r in grids[sheet][1:] if r and r[L_TIME]],
                      key=lambda r: r[L_TIME])
        equip = rows[0][L_EQUIP]
        info = master.get(equip)
        if info is None:
            if equip not in unknown_equipment:
                unknown_equipment.append(equip)
            continue

        runs = []
        for i, r in enumerate(rows):
            if str(r[L_LOC]).strip() != "설비":
                continue
            start = r[L_TIME]
            end = rows[i + 1][L_TIME] if i + 1 < len(rows) else None
            if end is None:
                open_runs += 1
                runs.append({"start": start, "end": None, "days": [],
                             "produced": None, "defects": None, "rate": None})
                continue
            n = max(1, math.ceil((end - start) / timedelta(hours=24)))
            days = [f"{(start + timedelta(days=k)).date():%Y-%m-%d}" for k in range(n)]
            produced = defects = 0
            for d in days:
                hit = mes.get((d, info["code"]))
                if hit is None:
                    if d not in have_days:
                        missing_days.add(d)
                    continue
                produced += hit[0]
                defects += hit[1]
            if produced == 0:
                unmatched += 1
            runs.append({
                "start": start, "end": end, "days": days,
                "produced": produced or None, "defects": defects or None,
                "rate": (defects / produced) if produced else None,
            })
        molds[info["mold"]] = {
            "sheet": sheet, "line": info["line"], "equip": equip,
            "last_location": rows[-1][L_LOC], "runs": runs,
        }

    # ── IQC: 금형번호만 뽑아 고아를 가려낸다 ─────────────────────────
    # detail 표가 둘이다. 대장 상세표(33행~)의 '금형 번호'와 이력표(17행~)의
    # '관리 번호' — 열 위치도 다르다. 하나만 읽으면 IQC붙음 이 실제보다 적게
    # 나와, 파이프라인이 맞는데도 틀린 것처럼 보인다.
    iqc_grid = _grid(root / "IQC" / "2026_금형측정대장.xlsx", "Sheet1")
    iqc_molds = set()
    for rows, col in ((iqc_grid[33:], Q_MOLD), (iqc_grid[16:21], Q_HIST_MOLD)):
        for r in rows:
            if r and len(r) > col and r[col]:
                no = str(r[col]).lstrip("#").strip()
                if no and no not in {"소계", "합계", "총계"}:
                    iqc_molds.add(no)
    orphans = sorted(iqc_molds - set(molds))
    matched = sorted(iqc_molds & set(molds))

    # ── 출력 ─────────────────────────────────────────────────────────
    print("=" * 74)
    print("LLM 없이 계산한 기대값 — 수집 결과가 이것과 달라야 할 이유는 없다")
    print("=" * 74)
    print(f"\n금형 {len(molds)}건  IQC붙음 {len(matched)}건")
    print(f"고아(관리대장에 없는 IQC 금형) {orphans}")
    print(f"기준정보에 없는 설비 {unknown_equipment}")
    print(f"MES 파일이 없는 날 {sorted(missing_days)}")
    print(f"가동 중(종료 없음) {open_runs}건   MES 실적 못 찾은 구간 {unmatched}건")

    print("\n" + "-" * 74)
    print(f"{'금형':10} {'상태(마지막 위치)':22} {'설치':>4} {'불량율':>9}  라인")
    print("-" * 74)
    for mold in sorted(molds):
        m = molds[mold]
        rate = next((r["rate"] for r in reversed(m["runs"]) if r["rate"]), None)
        shown = f"{rate*100:.3f}%" if rate else "—"
        print(f"{mold:10} {str(m['last_location']):22} "
              f"{len(m['runs']):>4} {shown:>9}  {m['line']}")

    print("\n" + "-" * 74)
    print("사용구간별 상세 (production_run 에 이 값이 들어가야 한다)")
    print("-" * 74)
    for mold in sorted(molds):
        for r in molds[mold]["runs"]:
            end = f"{r['end']:%m-%d %H:%M}" if r["end"] else "(가동 중)"
            hours = (r["end"] - r["start"]).total_seconds() / 3600 if r["end"] else 0
            rate = f"{r['rate']*100:.3f}%" if r["rate"] else "—"
            print(f"  {mold:10} {r['start']:%m-%d %H:%M} ~ {end:14} "
                  f"{hours:>6.1f}h  {len(r['days'])}일  "
                  f"투입 {r['produced'] or 0:>7,}  불량 {r['defects'] or 0:>5,}  {rate}")
            if r["days"]:
                gone = [d for d in r["days"] if d not in have_days]
                note = f"   ← {gone} 파일 없음" if gone else ""
                print(f"             {r['days']}{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
