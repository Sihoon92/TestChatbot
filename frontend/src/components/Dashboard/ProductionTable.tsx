import type { ProductionRun } from "../../types/mold";
import {
  DASH,
  fmtNumber,
  fmtPercent,
  fmtPeriod,
  fmtRunDays,
  fmtText,
} from "./formatters";

/** 화면에 있는 행들에 등장한 불량 항목의 합집합(처음 등장한 순서 유지).
 *
 * 불량 항목은 제품·시기마다 달라질 수 있어 고정 컬럼으로 잡지 않는다. 정렬하지
 * 않는 이유는 원본 파일의 항목 순서가 그 자체로 정보이기 때문이다. */
export function defectLabels(runs: ProductionRun[]): string[] {
  const seen: string[] = [];
  for (const run of runs) {
    for (const defect of run.defects) {
      if (!seen.includes(defect.label)) seen.push(defect.label);
    }
  }
  return seen;
}

const TH = "border-b border-paper-dark px-2 py-1.5 text-left font-semibold";
const TD = "border-b border-paper-dark/50 px-2 py-1.5";

export default function ProductionTable({ runs }: { runs: ProductionRun[] }) {
  if (runs.length === 0) {
    return <p className="p-4 text-sm text-ink/60">생산 이력이 없습니다</p>;
  }

  const labels = defectLabels(runs);

  return (
    <div className="overflow-x-auto p-3">
      <table className="w-full min-w-max text-sm">
        <thead>
          <tr>
            <th className={TH}>기간</th>
            <th className={TH}>라인</th>
            <th className={TH}>설비</th>
            <th className={TH}>일수</th>
            <th className={TH}>투입</th>
            <th className={TH}>불량</th>
            <th className={TH}>불량율</th>
            {labels.map((label) => (
              <th key={label} className={TH}>
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {runs.map((run, index) => {
            const byLabel = new Map(run.defects.map((d) => [d.label, d.rate]));
            // install_seq 만으로는 유일성이 보장되지 않는다 — 스펙에서
            // "한 행 = 한 설치"는 검증되지 않은 가정이라고 명시한다.
            // StageItemPanel 이 label 에 대해 하는 것과 같은 패턴으로
            // index 를 key 에 더한다.
            return (
              <tr key={`${run.install_seq}-${index}`}>
                <td className={TD}>{fmtPeriod(run.started_at, run.ended_at)}</td>
                <td className={TD}>{fmtText(run.line)}</td>
                <td className={TD}>{fmtText(run.machine)}</td>
                <td className={TD}>
                  {fmtRunDays(run.days_covered, run.days_expected, run.ended_at)}
                </td>
                <td className={TD}>{fmtNumber(run.produced)}</td>
                <td className={TD}>{fmtNumber(run.defect_count)}</td>
                {/* PPM 단위의 불량율이라 소수 첫째 자리로는 1.2%/1.3% 로 뭉개져
                    구간끼리 비교가 안 된다. 셋째 자리까지 보여준다. */}
                <td className={TD}>{fmtPercent(run.defect_rate, 3)}</td>
                {labels.map((label) => (
                  <td key={label} className={TD}>
                    {/* 이 행에 없는 항목은 0.0% 가 아니라 — 다. 0 으로 채우면
                        "그 불량이 없었다"는 잘못된 정보가 된다. */}
                    {byLabel.has(label) ? fmtPercent(byLabel.get(label)!) : DASH}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
