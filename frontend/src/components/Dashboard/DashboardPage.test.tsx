import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api/molds", () => ({
  listMolds: vi.fn(),
  getMold: vi.fn(),
  getFilterOptions: vi.fn(),
}));

import { getFilterOptions, getMold, listMolds } from "../../api/molds";
import { DEFAULT_FILTERS, useDashboardStore } from "../../store/dashboardStore";
import type { MoldDetail, MoldSummary } from "../../types/mold";
import DashboardPage from "./DashboardPage";

const SUMMARY: MoldSummary = {
  mold_no: "M-1024", status: "in_use", line: "3", machine: "2",
  shot_count: 8412, latest_defect_rate: 0.008, total_production: 1204500,
  stage_status: { design: "ok", iqc: "ok", pqc: "ok", install: "ok", ai_recheck: "ok" },
};

const DETAIL: MoldDetail = {
  summary: SUMMARY,
  design: {
    angle_deg: 12.5, height_mm: 45, step_mm: 0.8,
    overall_mm: 210, plate_height_mm: 120, plate_width_mm: 80,
  },
  history: { total_installs: 37, total_production: 1204500, first_installed_at: "2023-04-11" },
  current: { status: "in_use", line: "3", machine: "2", shot_count: 8412, installed_at: "2026-07-20" },
  productions: [],
  stages: [],
};

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/dashboard/:moldNo" element={<DashboardPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("DashboardPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listMolds).mockResolvedValue([SUMMARY]);
    vi.mocked(getMold).mockResolvedValue(DETAIL);
    vi.mocked(getFilterOptions).mockResolvedValue({
      statuses: ["in_use", "standby", "repair", "retired"],
      installations: [{ line: "3", machine: "2" }],
    });
    useDashboardStore.setState({
      filters: { ...DEFAULT_FILTERS }, molds: [], detail: null, error: null,
    });
  });

  it("loads filter options and the mold list on mount", async () => {
    renderAt("/dashboard");
    await waitFor(() => expect(getFilterOptions).toHaveBeenCalled());
    await waitFor(() => expect(listMolds).toHaveBeenCalled());
  });

  it("prompts the user to pick a mold when none is selected", async () => {
    renderAt("/dashboard");
    expect(await screen.findByText("왼쪽에서 금형을 선택하세요")).toBeInTheDocument();
    expect(getMold).not.toHaveBeenCalled();
  });

  it("loads the detail for the mold in the URL", async () => {
    renderAt("/dashboard/M-1024");
    await waitFor(() => expect(getMold).toHaveBeenCalledWith("M-1024"));
    expect(await screen.findByRole("heading", { name: "설계" })).toBeInTheDocument();
  });

  it("reloads the list when a filter changes", async () => {
    renderAt("/dashboard");
    await waitFor(() => expect(listMolds).toHaveBeenCalledTimes(1));
    useDashboardStore.getState().setFilter({ q: "M-10" });
    await waitFor(() => expect(listMolds).toHaveBeenCalledTimes(2));
  });

  it("shows an error banner with a retry button when loading fails", async () => {
    vi.mocked(listMolds).mockRejectedValue(new Error("HTTP 500"));
    renderAt("/dashboard");
    expect(await screen.findByText(/HTTP 500/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "다시 시도" })).toBeInTheDocument();
  });

  it("explains that the mold does not exist when the detail request 404s", async () => {
    vi.mocked(getMold).mockRejectedValue(new Error("HTTP 404"));
    renderAt("/dashboard/M-9999");
    expect(await screen.findByText(/HTTP 404/)).toBeInTheDocument();
  });
});
