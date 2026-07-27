import { STATUS_LABEL, type MoldDetail } from "../../types/mold";
import { fmtInstallation, fmtMeasure, fmtMeasurePair, fmtNumber, fmtText } from "./formatters";

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-2 text-sm">
      <span className="text-ink/60">{label}</span>
      <span className="font-medium">{value}</span>
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section
      aria-label={title}
      className="flex min-w-0 flex-1 flex-col gap-1 rounded-md border border-paper-dark bg-white p-3"
    >
      <h3 className="mb-1 text-xs font-semibold text-ink/70">{title}</h3>
      {children}
    </section>
  );
}

export default function SummaryCards({ detail }: { detail: MoldDetail }) {
  const { design, history, current } = detail;
  return (
    <div className="flex flex-wrap gap-2 p-3">
      <Card title="설계">
        <Row label="각도" value={fmtMeasure(design.angle_deg, "°")} />
        <Row label="높이" value={fmtMeasure(design.height_mm, "mm")} />
        <Row label="단차" value={fmtMeasure(design.step_mm, "mm")} />
        <Row label="전체" value={fmtMeasure(design.overall_mm, "mm")} />
        <Row
          label="Plate(높이×넓이)"
          value={fmtMeasurePair(design.plate_height_mm, design.plate_width_mm, "mm")}
        />
      </Card>

      <Card title="누적 이력">
        <Row label="총 설치 횟수" value={fmtNumber(history.total_installs)} />
        <Row label="총 생산 수량" value={fmtNumber(history.total_production)} />
        <Row label="최초 설치일" value={fmtText(history.first_installed_at)} />
      </Card>

      <Card title="현 상태">
        <Row label="상태" value={STATUS_LABEL[current.status]} />
        <Row label="설치 호기" value={fmtInstallation(current.line, current.machine)} />
        <Row label="사용 타수" value={fmtNumber(current.shot_count)} />
        <Row label="설치일" value={fmtText(current.installed_at)} />
      </Card>
    </div>
  );
}
