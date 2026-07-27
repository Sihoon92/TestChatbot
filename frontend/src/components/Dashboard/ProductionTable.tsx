import type { ProductionRun } from "../../types/mold";
import { DASH, fmtPercent, fmtText } from "./formatters";

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
            <th className={TH}>설치#</th>
            <th className={TH}>호기</th>
            <th className={TH}>시간대</th>
            <th className={TH}>연마결과</th>
            <th className={TH}>불량율</th>
            {labels.map((label) => (
              <th key={label} className={TH}>
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const byLabel = new Map(run.defects.map((d) => [d.label, d.rate]));
            return (
              <tr key={run.install_seq}>
                <td className={TD}>{run.install_seq}</td>
                <td className={TD}>
                  {run.line}-{run.machine}
                </td>
                <td className={TD}>
                  {run.started_at} ~ {fmtText(run.ended_at)}
                </td>
                <td className={TD}>{fmtText(run.grind_result)}</td>
                <td className={TD}>{fmtPercent(run.defect_rate)}</td>
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
