import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { MoldDetail } from "../../types/mold";
import StageTabs from "./StageTabs";

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
  productions: [
    {
      install_seq: 64, line: "1", machine: "4",
      started_at: "2026-05-02", ended_at: "2026-06-30",
      grind_result: "OK", defect_rate: 0.005,
      defects: [{ label: "버", rate: 0.005 }],
    },
  ],
  stages: [
    {
      stage: "iqc", status: "ok", updated_at: "2021-02-14", error: null,
      items: [
        {
          label: "경도", value: "HRC 60", judgment: "ok",
          source: { file: "IQC/2021-02-14_M-0998.xlsx", sheet: "검사", cell: "C12" },
        },
      ],
    },
    {
      stage: "pqc", status: "error", updated_at: null,
      error: "시트 '공정'을 찾지 못했습니다", items: [],
    },
    { stage: "ai_recheck", status: "missing", updated_at: null, error: null, items: [] },
  ],
};

describe("StageTabs", () => {
  it("renders all five tabs in display order", () => {
    render(<StageTabs detail={DETAIL} />);
    const tabs = screen.getAllByRole("tab").map((t) => t.textContent);
    expect(tabs).toEqual([
      "생산결과", "설계", "IQC", "PQC ⚠", "AI복검 ·",
    ]);
  });

  it("opens on the production tab", () => {
    render(<StageTabs detail={DETAIL} />);
    expect(screen.getByRole("tab", { name: "생산결과" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("switches to the design tab on click", async () => {
    render(<StageTabs detail={DETAIL} />);
    await userEvent.click(screen.getByRole("tab", { name: "설계" }));
    expect(screen.getByText("각도")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows the failure reason on a stage whose extraction errored", async () => {
    render(<StageTabs detail={DETAIL} />);
    await userEvent.click(screen.getByRole("tab", { name: "PQC ⚠" }));
    expect(screen.getByText(/시트 '공정'을 찾지 못했습니다/)).toBeInTheDocument();
  });

  it("shows a missing-document message on a stage with no data", async () => {
    render(<StageTabs detail={DETAIL} />);
    await userEvent.click(screen.getByRole("tab", { name: "AI복검 ·" }));
    expect(screen.getByText("아직 문서가 없습니다")).toBeInTheDocument();
  });

  it("shows the source reference as a tooltip on an extracted value", async () => {
    render(<StageTabs detail={DETAIL} />);
    await userEvent.click(screen.getByRole("tab", { name: "IQC" }));
    expect(screen.getByText("HRC 60")).toHaveAttribute(
      "title",
      "IQC/2021-02-14_M-0998.xlsx · 검사 · C12",
    );
  });
});
