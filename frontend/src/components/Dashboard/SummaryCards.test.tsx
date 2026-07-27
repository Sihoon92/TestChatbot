import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { MoldDetail } from "../../types/mold";
import SummaryCards from "./SummaryCards";

const DETAIL: MoldDetail = {
  summary: {
    mold_no: "M-0998", status: "standby", line: null, machine: null,
    shot_count: 0, latest_defect_rate: null, total_production: 2811300,
    stage_status: { design: "ok", iqc: "ok", pqc: "error", install: "ok", ai_recheck: "missing" },
  },
  design: {
    angle_deg: null, height_mm: 52, step_mm: null,
    overall_mm: 240, plate_height_mm: 140, plate_width_mm: null,
  },
  history: { total_installs: 64, total_production: 2811300, first_installed_at: "2021-02-15" },
  current: { status: "standby", line: null, machine: null, shot_count: 0, installed_at: null },
  productions: [],
  stages: [],
};

describe("SummaryCards", () => {
  it("renders the three cards", () => {
    render(<SummaryCards detail={DETAIL} />);
    expect(screen.getByRole("heading", { name: "설계" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "누적 이력" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "현 상태" })).toBeInTheDocument();
  });

  it("dashes design fields that were not extracted", () => {
    render(<SummaryCards detail={DETAIL} />);
    const card = screen.getByRole("region", { name: "설계" });
    expect(within(card).getByText("각도").nextSibling).toHaveTextContent("—");
    expect(within(card).getByText("높이").nextSibling).toHaveTextContent("52mm");
  });

  it("shows a real zero shot count as 0, not as a dash", () => {
    render(<SummaryCards detail={DETAIL} />);
    const card = screen.getByRole("region", { name: "현 상태" });
    expect(within(card).getByText("사용 타수").nextSibling).toHaveTextContent("0");
  });

  it("dashes the installation for a standby mold", () => {
    render(<SummaryCards detail={DETAIL} />);
    const card = screen.getByRole("region", { name: "현 상태" });
    expect(within(card).getByText("설치 호기").nextSibling).toHaveTextContent("—");
  });

  it("groups thousands in the cumulative production", () => {
    render(<SummaryCards detail={DETAIL} />);
    const card = screen.getByRole("region", { name: "누적 이력" });
    expect(within(card).getByText("총 생산 수량").nextSibling).toHaveTextContent("2,811,300");
  });

  it("dashes the Plate row when either dimension is null", () => {
    render(<SummaryCards detail={DETAIL} />);
    const card = screen.getByRole("region", { name: "설계" });
    expect(within(card).getByText("Plate(높이×넓이)").nextSibling).toHaveTextContent("—");
  });

  it("renders both Plate dimensions when both are present", () => {
    const moldWithBothPlates = {
      ...DETAIL,
      design: { ...DETAIL.design, plate_height_mm: 140, plate_width_mm: 80 },
    };
    render(<SummaryCards detail={moldWithBothPlates} />);
    const card = screen.getByRole("region", { name: "설계" });
    expect(within(card).getByText("Plate(높이×넓이)").nextSibling).toHaveTextContent("140×80mm");
  });
});
