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
      listError: null,
      detailError: null,
      _optionsError: null,
      _moldsError: null,
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
    expect(useDashboardStore.getState().listError).toContain("HTTP 500");
    expect(useDashboardStore.getState().listLoading).toBe(false);
  });

  // 예전 테스트("clears a previous error once a request succeeds")는 error
  // 필드 하나를 세 loader 가 공유하던 버그를 "의도된 동작"처럼 검증하고
  // 있었다 — loadMolds 의 성공이 (그 자신과 무관한) detailError 까지
  // 지워버리는 것을 정상으로 취급한 것이다. 전체 리뷰에서 확인된 실제
  // 재현 케이스: /dashboard/M-9999 로 들어가 상세 조회가 404 로 실패한 뒤
  // 필터를 바꾸면(목록 조회는 성공) 상세 패널의 에러가 사라져 "불러오는
  // 중…"에 영구히 멈춘다. 이제는 listError/detailError 가 분리되어 있으니,
  // "한 loader의 성공이 다른 loader의 실패를 지우지 않는다"는 격리
  // 자체를 검증해야 한다.
  it("does not let a loadMolds success clear a live detailError", async () => {
    useDashboardStore.setState({ detailError: "상세 조회 실패" });
    vi.mocked(listMolds).mockResolvedValueOnce([]);
    await useDashboardStore.getState().loadMolds();
    expect(useDashboardStore.getState().detailError).toBe("상세 조회 실패");
  });

  it("does not let a loadDetail success clear a live listError", async () => {
    useDashboardStore.setState({ listError: "목록 조회 실패" });
    const fakeDetail = { summary: { mold_no: "M-10" } } as unknown as MoldDetail;
    vi.mocked(getMold).mockResolvedValueOnce(fakeDetail);
    await useDashboardStore.getState().loadDetail("M-10");
    expect(useDashboardStore.getState().listError).toBe("목록 조회 실패");
  });

  // Finding(전체 리뷰) 회귀 테스트: /dashboard/M-9999 로 직접 들어가면 패널이
  // "불러오지 못했습니다"를 정확히 보여준다. 그 후 필터를 아무거나 바꾸면
  // loadMolds 가 성공하는데, 이때 detailError 가 지워지면 패널은 실패도
  // 로딩도 아닌 상태로 떨어져 "불러오는 중…"에 영구히 멈춘다(detailLoading
  // 은 이미 false 다) — 사용자는 그 금형이 없다는 사실을 영영 알 수 없다.
  it("keeps a detail 404 visible after an unrelated loadMolds success (regression: stuck on 불러오는 중…)", async () => {
    vi.mocked(getMold).mockRejectedValueOnce(new Error("HTTP 404"));
    await useDashboardStore.getState().loadDetail("M-9999");
    expect(useDashboardStore.getState().detailError).toContain("HTTP 404");
    expect(useDashboardStore.getState().detailLoading).toBe(false);

    vi.mocked(listMolds).mockResolvedValueOnce([]);
    await useDashboardStore.getState().loadMolds();

    const state = useDashboardStore.getState();
    expect(state.detailError).toContain("HTTP 404");
    expect(state.detailLoading).toBe(false);
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
    expect(useDashboardStore.getState().listError).toContain("HTTP 500");
  });

  // 전체 리뷰 케이스 (b): loadOptions 가 실패한 채로 loadMolds 가 성공하면,
  // listError 는 두 loader가 공유하는 필드지만 loadMolds 의 성공이 그
  // 원인이 아닌 loadOptions 의 실패까지 지워서는 안 된다 — 그러면 배너가
  // 사라지고 options 는 null 로 남는데 화면은 조용히 "정상"인 척한다.
  it("keeps a loadOptions failure visible through a later loadMolds success", async () => {
    vi.mocked(getFilterOptions).mockRejectedValueOnce(new Error("HTTP 500"));
    await useDashboardStore.getState().loadOptions();
    expect(useDashboardStore.getState().listError).toContain("HTTP 500");

    vi.mocked(listMolds).mockResolvedValueOnce([]);
    await useDashboardStore.getState().loadMolds();
    expect(useDashboardStore.getState().listError).toContain("HTTP 500");
  });

  // 대칭 케이스: loadMolds 가 실패한 채로 loadOptions 가 성공해도 마찬가지다.
  it("keeps a loadMolds failure visible through a later loadOptions success", async () => {
    vi.mocked(listMolds).mockRejectedValueOnce(new Error("HTTP 500"));
    await useDashboardStore.getState().loadMolds();
    expect(useDashboardStore.getState().listError).toContain("HTTP 500");

    vi.mocked(getFilterOptions).mockResolvedValueOnce({ statuses: [], installations: [] });
    await useDashboardStore.getState().loadOptions();
    expect(useDashboardStore.getState().listError).toContain("HTTP 500");
  });

  it("populates detail from getMold on success", async () => {
    const fakeDetail = { summary: { mold_no: "M-10" } } as unknown as MoldDetail;
    vi.mocked(getMold).mockResolvedValueOnce(fakeDetail);
    await useDashboardStore.getState().loadDetail("M-10");
    const state = useDashboardStore.getState();
    expect(state.detail).toEqual(fakeDetail);
    expect(state.detailError).toBeNull();
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
    expect(state.detailError).toContain("HTTP 404");
    expect(state.detailLoading).toBe(false);
  });

  // 재리뷰 finding B1(회귀): detail 만 지우고 detailError 를 남겨두면, 404로
  // 실패한 채로 "목록으로 돌아가기"를 눌러 moldNo 가 undefined 로 바뀌어도
  // (DashboardPage 의 moldNo 이펙트가 clearDetail() 을 부른다) 배너의
  // detailError 가 영구히 남는다 — 필터 변경은 listError 만 건드리고,
  // moldNo === undefined 일 때는 다시 시도도 loadDetail 을 부르지 않기
  // 때문이다. error 를 listError/detailError 로 쪼개기 전에는 다음
  // loadMolds 성공이 공유 필드를 지워 저절로 나았던 상태다.
  it("clears detailError along with detail, so a stale 404 does not survive clearDetail", () => {
    useDashboardStore.setState({ detail: null, detailError: "HTTP 404" });
    useDashboardStore.getState().clearDetail();
    expect(useDashboardStore.getState().detailError).toBeNull();
  });
});
