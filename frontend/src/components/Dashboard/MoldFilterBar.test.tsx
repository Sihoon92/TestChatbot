import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api/molds", () => ({
  listMolds: vi.fn(async () => []),
  getMold: vi.fn(async () => null),
  getFilterOptions: vi.fn(async () => ({ statuses: [], installations: [] })),
}));

import { DEFAULT_FILTERS, useDashboardStore } from "../../store/dashboardStore";
import MoldFilterBar from "./MoldFilterBar";

const OPTIONS = {
  statuses: ["in_use", "standby", "repair", "retired"] as const,
  installations: [
    { line: "3", machine: "2" },
    { line: "3", machine: "5" },
  ],
};

describe("MoldFilterBar", () => {
  beforeEach(() => {
    useDashboardStore.setState({
      filters: { ...DEFAULT_FILTERS },
      options: { statuses: [...OPTIONS.statuses], installations: OPTIONS.installations },
    });
  });

  it("shows the installation dropdown while status is 사용중", () => {
    render(<MoldFilterBar />);
    expect(screen.getByLabelText("라인/호기")).toBeInTheDocument();
  });

  it("hides the installation dropdown when status is not 사용중", async () => {
    render(<MoldFilterBar />);
    await userEvent.selectOptions(screen.getByLabelText("금형 상태"), "standby");
    expect(screen.queryByLabelText("라인/호기")).not.toBeInTheDocument();
  });

  it("clears the installation filter when the dropdown disappears", async () => {
    render(<MoldFilterBar />);
    await userEvent.selectOptions(screen.getByLabelText("라인/호기"), "3-2");
    expect(useDashboardStore.getState().filters.line).toBe("3");

    await userEvent.selectOptions(screen.getByLabelText("금형 상태"), "repair");
    expect(useDashboardStore.getState().filters.line).toBeNull();
    expect(useDashboardStore.getState().filters.machine).toBeNull();
  });

  it("offers only installation pairs that exist in the data", () => {
    render(<MoldFilterBar />);
    const options = Array.from(
      screen.getByLabelText("라인/호기").querySelectorAll("option"),
    ).map((o) => o.textContent);
    // 전체 + 실제 존재하는 두 조합. "1-5" 같은 없는 조합이 나오면 안 된다.
    expect(options).toEqual(["전체", "3-2", "3-5"]);
  });

  it("includes 전체 in the status dropdown even though it is not a MoldStatus", () => {
    render(<MoldFilterBar />);
    const options = Array.from(
      screen.getByLabelText("금형 상태").querySelectorAll("option"),
    ).map((o) => o.textContent);
    expect(options).toEqual(["전체", "사용중", "대기중", "수리중", "폐기"]);
  });

  it("writes the search box value into the store", async () => {
    render(<MoldFilterBar />);
    await userEvent.type(screen.getByLabelText("금형 번호 검색"), "M-10");
    expect(useDashboardStore.getState().filters.q).toBe("M-10");
  });
});
