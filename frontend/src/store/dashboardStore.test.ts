import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/molds", () => ({
  listMolds: vi.fn(async () => []),
  getMold: vi.fn(async () => null),
  getFilterOptions: vi.fn(async () => ({ statuses: [], installations: [] })),
}));

import { listMolds } from "../api/molds";
import { DEFAULT_FILTERS, useDashboardStore } from "./dashboardStore";

describe("dashboardStore", () => {
  beforeEach(() => {
    useDashboardStore.setState({
      filters: { ...DEFAULT_FILTERS },
      molds: [],
      detail: null,
      error: null,
    });
    vi.clearAllMocks();
  });

  it("defaults to 사용중 so the installation dropdown is visible on open", () => {
    expect(DEFAULT_FILTERS.status).toBe("in_use");
  });

  it("clears line/machine when status moves away from in_use", () => {
    const st = useDashboardStore.getState();
    st.setFilter({ line: "3", machine: "2" });
    st.setFilter({ status: "standby" });
    const { filters } = useDashboardStore.getState();
    expect(filters.line).toBeNull();
    expect(filters.machine).toBeNull();
  });

  it("keeps line/machine when status stays in_use", () => {
    const st = useDashboardStore.getState();
    st.setFilter({ line: "3", machine: "2" });
    st.setFilter({ q: "M-10" });
    const { filters } = useDashboardStore.getState();
    expect(filters.line).toBe("3");
    expect(filters.machine).toBe("2");
  });

  it("restores every filter to its default on reset", () => {
    const st = useDashboardStore.getState();
    st.setFilter({ q: "zzz", status: "retired" });
    st.resetFilters();
    expect(useDashboardStore.getState().filters).toEqual(DEFAULT_FILTERS);
  });

  it("stores an error message when the list request fails", async () => {
    vi.mocked(listMolds).mockRejectedValueOnce(new Error("HTTP 500"));
    await useDashboardStore.getState().loadMolds();
    expect(useDashboardStore.getState().error).toContain("HTTP 500");
    expect(useDashboardStore.getState().listLoading).toBe(false);
  });

  it("clears a previous error once a request succeeds", async () => {
    useDashboardStore.setState({ error: "옛 오류" });
    await useDashboardStore.getState().loadMolds();
    expect(useDashboardStore.getState().error).toBeNull();
  });
});
