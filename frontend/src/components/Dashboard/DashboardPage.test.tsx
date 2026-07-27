import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

const SUMMARY_1031: MoldSummary = { ...SUMMARY, mold_no: "M-1031" };
const DETAIL_1031: MoldDetail = { ...DETAIL, summary: SUMMARY_1031 };

/** 테스트가 직접 resolve 시점을 통제할 수 있는 프라미스. 타이머가 아니라
 * 명시적 resolve 순서로 경쟁 조건을 결정적으로 재현하기 위해 쓴다. */
function createDeferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

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
      filters: { ...DEFAULT_FILTERS }, molds: [], detail: null,
      listError: null, detailError: null, _optionsError: null, _moldsError: null,
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
    expect(await screen.findByText(/M-9999.*존재하지 않는 금형 번호/)).toBeInTheDocument();
    const backLink = screen.getByRole("link", { name: "목록으로 돌아가기" });
    expect(backLink).toHaveAttribute("href", "/dashboard");
  });

  it("retrying after a failure re-requests filter options too, not just the list and detail", async () => {
    vi.mocked(listMolds).mockRejectedValue(new Error("HTTP 500"));
    renderAt("/dashboard");
    await screen.findByText(/HTTP 500/);
    vi.mocked(getFilterOptions).mockClear();
    vi.mocked(listMolds).mockResolvedValue([SUMMARY]);

    await userEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    expect(getFilterOptions).toHaveBeenCalled();
  });

  // Finding 1 회귀 테스트: M-1024 를 보다가 M-1031 을 고르면, M-1031 요청이
  // 아직 끝나지 않은 그 순간에도 화면에 M-1024 의 상세가 남아 있으면 안
  // 된다 — 남아 있으면 URL/목록의 선택 표시와 상세 패널이 서로 다른 금형을
  // 가리키는 상태가 요청이 끝날 때까지 보인다는 뜻이다. 요청을 수동으로
  // 통제하는 프라미스로 묶어 그 "요청 도중" 프레임을 직접 검사한다.
  it("clears the previous mold's detail immediately on selection, before the new request resolves", async () => {
    vi.mocked(listMolds).mockResolvedValue([SUMMARY, SUMMARY_1031]);
    const deferred1031 = createDeferred<MoldDetail>();
    vi.mocked(getMold)
      .mockResolvedValueOnce(DETAIL) // 최초 진입: M-1024
      .mockImplementationOnce(() => deferred1031.promise); // M-1031 선택: 아직 응답 안 함

    renderAt("/dashboard/M-1024");
    expect(await screen.findByRole("heading", { name: "M-1024", level: 2 })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /M-1031/ }));

    // M-1031 요청이 아직 끝나지 않았다 — 이 시점에 M-1024 상세가 보이면 안 된다.
    expect(screen.queryByRole("heading", { name: "M-1024", level: 2 })).not.toBeInTheDocument();

    deferred1031.resolve(DETAIL_1031);
    expect(await screen.findByRole("heading", { name: "M-1031", level: 2 })).toBeInTheDocument();
  });

  // Finding 1 회귀 테스트(경쟁 조건): 빠르게 M-A 를 고르고 곧바로 M-B 를
  // 고르면 두 loadDetail 호출이 동시에 진행된다. 먼저 보낸 M-A 요청이 나중에
  // 응답하더라도, 최신 선택인 M-B 의 데이터가 스토어에 남아야 한다. 타이머가
  // 아니라 수동으로 통제하는 프라미스로 응답 순서를 결정적으로 뒤집는다.
  it("keeps the latest selection's detail even when an earlier request resolves later (race)", async () => {
    const deferredA = createDeferred<MoldDetail>();
    const deferredB = createDeferred<MoldDetail>();
    vi.mocked(getMold).mockImplementationOnce(() => deferredA.promise).mockImplementationOnce(() => deferredB.promise);

    const DETAIL_A: MoldDetail = { ...DETAIL, summary: { ...SUMMARY, mold_no: "M-A" } };
    const DETAIL_B: MoldDetail = { ...DETAIL, summary: { ...SUMMARY, mold_no: "M-B" } };

    const store = useDashboardStore.getState();
    const loadA = store.loadDetail("M-A");
    const loadB = store.loadDetail("M-B");

    // 나중에 고른 B 가 먼저 응답한다.
    deferredB.resolve(DETAIL_B);
    await loadB;
    // 먼저 고른 A 가 뒤늦게 응답한다 — 이게 store 를 덮어쓰면 안 된다.
    deferredA.resolve(DETAIL_A);
    await loadA;

    expect(useDashboardStore.getState().detail?.summary.mold_no).toBe("M-B");
  });
});
