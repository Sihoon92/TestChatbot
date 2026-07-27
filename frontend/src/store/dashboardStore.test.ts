import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/molds", () => ({
  listMolds: vi.fn(async () => []),
  getMold: vi.fn(async () => null),
  getFilterOptions: vi.fn(async () => ({ statuses: [], installations: [] })),
}));

import { getFilterOptions, getMold, listMolds } from "../api/molds";
import { DEFAULT_FILTERS, useDashboardStore } from "./dashboardStore";
import type { FilterOptions, MoldDetail } from "../types/mold";

describe("dashboardStore", () => {
  beforeEach(() => {
    useDashboardStore.setState({
      filters: { ...DEFAULT_FILTERS },
      molds: [],
      detail: null,
      options: null,
      detailLoading: false,
      error: null,
    });
    vi.clearAllMocks();
  });

  it("defaults to 사용중 so the installation dropdown is visible on open", () => {
    expect(DEFAULT_FILTERS.status).toBe("in_use");
  });

  it("defaults listLoading to true, since the dashboard always loads on mount", async () => {
    // beforeEach 는 filters/molds/detail/options/detailLoading/error 만 리셋하고
    // listLoading 은 건드리지 않는다 — 앞선 테스트들이 loadMolds()를 실행하며
    // 이 싱글턴의 listLoading 을 이미 true/false 로 바꿔놨을 수 있어, 현재
    // getState() 를 읽으면 모듈 최초 생성 시점의 값을 증명하지 못한다.
    // vi.resetModules() 로 모듈 레지스트리를 비우고 다시 import 해 신선한
    // 스토어 인스턴스를 만들어야, "마운트 전에는 로딩 중"이라는 문서화된
    // 기본값을 실제로 검증할 수 있다.
    vi.resetModules();
    const fresh = await import("./dashboardStore");
    expect(fresh.useDashboardStore.getState().listLoading).toBe(true);
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

  it("clears line/machine when a single patch sets both them and a non-in_use status", () => {
    const st = useDashboardStore.getState();
    st.setFilter({ status: "standby", line: "3", machine: "2" });
    const { filters } = useDashboardStore.getState();
    expect(filters.line).toBeNull();
    expect(filters.machine).toBeNull();
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

  it("populates options from getFilterOptions on success", async () => {
    const options: FilterOptions = {
      statuses: ["in_use"],
      installations: [{ line: "3", machine: "2" }],
    };
    vi.mocked(getFilterOptions).mockResolvedValueOnce(options);
    await useDashboardStore.getState().loadOptions();
    expect(useDashboardStore.getState().options).toEqual(options);
  });

  it("stores an error message when loadOptions fails", async () => {
    vi.mocked(getFilterOptions).mockRejectedValueOnce(new Error("HTTP 500"));
    await useDashboardStore.getState().loadOptions();
    expect(useDashboardStore.getState().error).toContain("HTTP 500");
  });

  it("populates detail from getMold on success", async () => {
    const fakeDetail = { summary: { mold_no: "M-10" } } as unknown as MoldDetail;
    vi.mocked(getMold).mockResolvedValueOnce(fakeDetail);
    await useDashboardStore.getState().loadDetail("M-10");
    const state = useDashboardStore.getState();
    expect(state.detail).toEqual(fakeDetail);
    expect(state.error).toBeNull();
    expect(state.detailLoading).toBe(false);
  });

  it("clears a stale detail when loadDetail fails, so the previous mold's data is not shown", async () => {
    // 이전에 선택한 금형의 상세가 남아 있으면, 실패 후에도 그 값이 새로
    // 선택한 금형의 것처럼 보일 수 있다 — 반드시 비워야 한다.
    useDashboardStore.setState({ detail: { summary: { mold_no: "OLD" } } as unknown as MoldDetail });
    vi.mocked(getMold).mockRejectedValueOnce(new Error("HTTP 404"));
    await useDashboardStore.getState().loadDetail("M-99");
    const state = useDashboardStore.getState();
    expect(state.detail).toBeNull();
    expect(state.error).toContain("HTTP 404");
    expect(state.detailLoading).toBe(false);
  });
});
