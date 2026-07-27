import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api/molds", () => ({
  listMolds: vi.fn(async () => []),
  getMold: vi.fn(async () => null),
  getFilterOptions: vi.fn(async () => ({ statuses: [], installations: [] })),
}));

import { DEFAULT_FILTERS, useDashboardStore } from "../../store/dashboardStore";
import type { MoldSummary } from "../../types/mold";
import MoldList from "./MoldList";

const OK_STAGES = {
  design: "ok", iqc: "ok", pqc: "ok", install: "ok", ai_recheck: "ok",
} as const;

const IN_USE: MoldSummary = {
  mold_no: "M-1024", status: "in_use", line: "3", machine: "2",
  shot_count: 8412, latest_defect_rate: 0.008, total_production: 1204500,
  stage_status: { ...OK_STAGES },
};

const STANDBY: MoldSummary = {
  mold_no: "M-0998", status: "standby", line: null, machine: null,
  shot_count: 0, latest_defect_rate: null, total_production: 2811300,
  stage_status: { ...OK_STAGES },
};

function LocationProbe() {
  return <span data-testid="path">{useLocation().pathname}</span>;
}

function renderList(molds: MoldSummary[], path = "/dashboard") {
  useDashboardStore.setState({ molds, filters: { ...DEFAULT_FILTERS }, listLoading: false });
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/dashboard" element={<><MoldList /><LocationProbe /></>} />
        <Route path="/dashboard/:moldNo" element={<><MoldList /><LocationProbe /></>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("MoldList", () => {
  beforeEach(() => useDashboardStore.setState({ molds: [] }));

  it("renders one row per mold", () => {
    renderList([IN_USE, STANDBY]);
    expect(screen.getByRole("button", { name: /M-1024/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /M-0998/ })).toBeInTheDocument();
  });

  it("navigates to the mold detail URL on click", async () => {
    renderList([IN_USE]);
    await userEvent.click(screen.getByRole("button", { name: /M-1024/ }));
    expect(screen.getByTestId("path")).toHaveTextContent("/dashboard/M-1024");
  });

  it("shows the installation as line-machine for an in-use mold", () => {
    renderList([IN_USE]);
    expect(screen.getByRole("button", { name: /3-2/ })).toBeInTheDocument();
  });

  it("shows a dash instead of an installation for a standby mold", () => {
    renderList([STANDBY]);
    const row = screen.getByRole("button", { name: /M-0998/ });
    expect(row).toHaveTextContent("—");
  });

  it("distinguishes a null defect rate from zero", () => {
    renderList([STANDBY]);
    const row = screen.getByRole("button", { name: /M-0998/ });
    expect(row).not.toHaveTextContent("0.0%");
  });

  it("marks the selected mold with aria-current", () => {
    renderList([IN_USE, STANDBY], "/dashboard/M-1024");
    expect(screen.getByRole("button", { name: /M-1024/ })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("button", { name: /M-0998/ })).not.toHaveAttribute("aria-current");
  });

  it("shows an empty-state message with a reset button when nothing matches", async () => {
    useDashboardStore.setState({ filters: { ...DEFAULT_FILTERS, q: "zzz" } });
    renderList([]);
    expect(screen.getByText("조건에 맞는 금형이 없습니다")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "필터 초기화" }));
    expect(useDashboardStore.getState().filters).toEqual(DEFAULT_FILTERS);
  });
});
