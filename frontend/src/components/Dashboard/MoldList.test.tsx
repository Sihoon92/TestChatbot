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
import type { MoldFilters, MoldSummary } from "../../types/mold";
import { DASH } from "./formatters";
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

function renderList(molds: MoldSummary[], path = "/dashboard", filters: MoldFilters = DEFAULT_FILTERS) {
  useDashboardStore.setState({ molds, filters: { ...filters }, listLoading: false });
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
    // 문자열 부분 매치라도 "불량 —" 자리에 정확히 대시가 와야 한다 — 단순히
    // "0.0%"가 아님만 확인하면 shot_count 를 잘못 넣는 실수도 통과해 버린다.
    expect(row).toHaveTextContent(`불량 ${DASH}`);
  });

  it("marks the selected mold with aria-current", () => {
    renderList([IN_USE, STANDBY], "/dashboard/M-1024");
    expect(screen.getByRole("button", { name: /M-1024/ })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("button", { name: /M-0998/ })).not.toHaveAttribute("aria-current");
  });

  it("shows an empty-state message with a reset button when nothing matches", async () => {
    renderList([], "/dashboard", { ...DEFAULT_FILTERS, q: "zzz" });
    expect(screen.getByText("조건에 맞는 금형이 없습니다")).toBeInTheDocument();
    // 클릭 전에 필터가 기본값이 아님을 먼저 확인해야, 클릭이 실제로 무언가를
    // 했다는 것이 증명된다 — 그렇지 않으면 onClick 배선이 빠져도 통과한다.
    expect(useDashboardStore.getState().filters).not.toEqual(DEFAULT_FILTERS);

    await userEvent.click(screen.getByRole("button", { name: "필터 초기화" }));
    expect(useDashboardStore.getState().filters).toEqual(DEFAULT_FILTERS);
  });

  // 전체 리뷰 케이스 (c): loadMolds 가 네트워크 실패로 비어 있는 것과
  // "조건에 맞는 금형이 없어서" 비어 있는 것은 다른 사실이다. 실패일 때
  // 필터 초기화 버튼을 보여주면, 문제였던 적 없는 필터를 고치라고
  // 유도하는 오판이 된다 — 페이지 상단 배너가 이미 실패를 알리므로
  // 여기서는 필터 초기화를 권하지 않는다.
  it("does not offer a filter reset when the list is empty because loading failed", () => {
    useDashboardStore.setState({
      molds: [],
      filters: { ...DEFAULT_FILTERS },
      listLoading: false,
      listError: "HTTP 500",
    });
    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <Routes>
          <Route path="/dashboard" element={<MoldList />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.queryByText("조건에 맞는 금형이 없습니다")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "필터 초기화" })).not.toBeInTheDocument();
  });
});
