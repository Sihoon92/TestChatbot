import type { DesignSpec } from "../../types/mold";
import { fmtMeasure } from "./formatters";

export default function DesignPanel({ design }: { design: DesignSpec }) {
  const rows: [string, string][] = [
    ["각도", fmtMeasure(design.angle_deg, "°")],
    ["높이", fmtMeasure(design.height_mm, "mm")],
    ["단차", fmtMeasure(design.step_mm, "mm")],
    ["전체", fmtMeasure(design.overall_mm, "mm")],
    ["Plate 높이", fmtMeasure(design.plate_height_mm, "mm")],
    ["Plate 넓이", fmtMeasure(design.plate_width_mm, "mm")],
  ];
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-2 p-4 text-sm sm:grid-cols-3">
      {rows.map(([label, value]) => (
        <div key={label} className="flex justify-between gap-2">
          <dt className="text-ink/60">{label}</dt>
          <dd className="font-medium">{value}</dd>
        </div>
      ))}
    </dl>
  );
}
