import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import IngestPanel from "./IngestPanel";
import type { RunSummary } from "../../api/ingest";

vi.mock("../../api/ingest", async () => ({
  runIngest: vi.fn(),
  getIngestStatus: vi.fn(),
}));

// 수집이 끝나면 패널이 목록을 다시 읽는다. 스토어를 모킹하지 않으면 진짜
// molds API 를 부르려 해서 테스트가 네트워크에 의존하게 된다.
const store = vi.hoisted(() => ({
  loadMolds: vi.fn(),
  loadOptions: vi.fn(),
}));
vi.mock("../../store/dashboardStore", () => ({
  useDashboardStore: (select: (s: typeof store) => unknown) => select(store),
}));

const api = await import("../../api/ingest");

const OK: RunSummary = {
  status: "ok",
  started_at: "2026-07-28T09:00:00",
  finished_at: "2026-07-28T09:00:12",
  mold_count: 4,
  iqc_matched: 3,
  orphan_mold_nos: [],
  unknown_statuses: [],
  unknown_status_rows: 0,
  skipped_rows: 0,
  files: ["MES/mes.xlsx"],
  error: null,
  unreadable_files: [],
  failed_files: [],
};

beforeEach(() => {
  // mock 호출 횟수가 테스트 사이에 누적되면 "실행 중에는 버튼을 잠근다"
  // 테스트의 toHaveBeenCalledTimes(1) 이 이전 테스트들의 호출까지 셈해
  // 거짓으로 실패한다. DashboardPage.test.tsx 와 같은 관례로 매번 비운다.
  vi.clearAllMocks();
  vi.mocked(api.getIngestStatus).mockResolvedValue(null);
  vi.mocked(api.runIngest).mockResolvedValue(OK);
});

describe("IngestPanel", () => {
  it("최초에는 실행 이력이 없다고 알린다", async () => {
    render(<IngestPanel />);
    expect(await screen.findByText(/아직 수집한 적이 없습니다/)).toBeInTheDocument();
  });

  it("버튼을 누르면 수집을 실행하고 결과를 보여준다", async () => {
    render(<IngestPanel />);
    await userEvent.click(await screen.findByRole("button", { name: /수집 실행/ }));

    await waitFor(() => expect(api.runIngest).toHaveBeenCalled());
    expect(await screen.findByText(/금형 4건/)).toBeInTheDocument();
  });

  it("손실 건수를 감추지 않는다", async () => {
    vi.mocked(api.runIngest).mockResolvedValue({
      ...OK,
      orphan_mold_nos: ["RX99999"],
      unknown_statuses: ["가동"],
      skipped_rows: 2,
    });

    render(<IngestPanel />);
    await userEvent.click(await screen.findByRole("button", { name: /수집 실행/ }));

    expect(await screen.findByText(/MES에 없는 금형 1건/)).toBeInTheDocument();
    expect(screen.getByText(/RX99999/)).toBeInTheDocument();
    expect(screen.getByText(/인식하지 못한 상태/)).toBeInTheDocument();
    expect(screen.getByText(/가동/)).toBeInTheDocument();
    expect(screen.getByText(/건너뛴 행 2건/)).toBeInTheDocument();
  });

  it("인식하지 못한 상태로 몇 건이 빠졌는지 보여준다", async () => {
    // 원문만 보여주면 "그래서 몇 건이 사라졌는데?"에 답이 없다. 실물 어휘가
    // STATUS_MAP 밖이면 목록이 통째로 비는데, 화면에는 작은 한 줄만 남는다.
    vi.mocked(api.runIngest).mockResolvedValue({
      ...OK,
      unknown_statuses: ["가동", "휴지"],
      unknown_status_rows: 42,
    });

    render(<IngestPanel />);
    await userEvent.click(await screen.findByRole("button", { name: /수집 실행/ }));

    expect(
      await screen.findByText(/인식하지 못한 상태: 가동, 휴지 \(이 상태의 행 42건이 제외됨\)/)
    ).toBeInTheDocument();
  });

  it("파싱에 실패한 파일과 사유를 보여준다", async () => {
    // 배치는 성공(status="ok")이므로 여기서 안 보여주면 그 파일의 IQC
    // 항목이 사유 없이 사라진다.
    vi.mocked(api.runIngest).mockResolvedValue({
      ...OK,
      failed_files: ["IQC/입고검사.xlsx: ValueError: 컬럼 매핑이 없다"],
    });

    render(<IngestPanel />);
    await userEvent.click(await screen.findByRole("button", { name: /수집 실행/ }));

    expect(await screen.findByText(/읽지 못한 파일 1건/)).toBeInTheDocument();
    expect(
      screen.getByText("IQC/입고검사.xlsx: ValueError: 컬럼 매핑이 없다")
    ).toBeInTheDocument();
  });

  it("수집이 끝나면 금형 목록과 필터 선택지를 다시 읽는다", async () => {
    // 수집은 DB 를 통째로 갈아치운다. 다시 읽지 않으면 패널에는 "금형 4건"이
    // 뜨는데 왼쪽 목록은 비어 있는 상태가 남는다.
    render(<IngestPanel />);
    await userEvent.click(await screen.findByRole("button", { name: /수집 실행/ }));

    await waitFor(() => expect(store.loadMolds).toHaveBeenCalled());
    expect(store.loadOptions).toHaveBeenCalled();
  });

  it("건너뛰거나 실패해도 목록을 다시 읽는다", async () => {
    // ok 일 때만 부르면, 직전 실패로 목록이 비어 있는 상태에서 벗어날 수 없다.
    vi.mocked(api.runIngest).mockResolvedValue({ ...OK, status: "skipped" });

    render(<IngestPanel />);
    await userEvent.click(await screen.findByRole("button", { name: /수집 실행/ }));

    await waitFor(() => expect(store.loadMolds).toHaveBeenCalled());
  });

  it("실패 사유를 그대로 보여준다", async () => {
    vi.mocked(api.runIngest).mockResolvedValue({
      ...OK, status: "error", error: "MES 파일이 없다",
    });

    render(<IngestPanel />);
    await userEvent.click(await screen.findByRole("button", { name: /수집 실행/ }));

    expect(await screen.findByText(/MES 파일이 없다/)).toBeInTheDocument();
  });

  it("실행 중에는 버튼을 잠근다", async () => {
    let resolve: (v: RunSummary) => void = () => {};
    vi.mocked(api.runIngest).mockReturnValue(
      new Promise<RunSummary>((r) => { resolve = r; })
    );

    render(<IngestPanel />);
    const button = await screen.findByRole("button", { name: /수집 실행/ });
    await userEvent.click(button);

    expect(await screen.findByRole("button", { name: /수집 중/ })).toBeDisabled();
    resolve(OK);
    await waitFor(() => expect(api.runIngest).toHaveBeenCalledTimes(1));
  });

  it("변경 없이 건너뛴 경우에는 파일을 못 읽었다는 경고를 보여주지 않는다", async () => {
    vi.mocked(api.runIngest).mockResolvedValue({
      ...OK, status: "skipped", unreadable_files: [],
    });

    render(<IngestPanel />);
    await userEvent.click(await screen.findByRole("button", { name: /수집 실행/ }));

    expect(await screen.findByText(/변경된 파일이 없어 건너뛰었습니다/)).toBeInTheDocument();
  });

  it("파일을 못 읽어 건너뛴 경우에는 어느 파일인지 보여준다", async () => {
    vi.mocked(api.runIngest).mockResolvedValue({
      ...OK,
      status: "skipped",
      unreadable_files: ["MES/mes.xlsx"],
    });

    render(<IngestPanel />);
    await userEvent.click(await screen.findByRole("button", { name: /수집 실행/ }));

    expect(await screen.findByText(/읽지 못한 파일이 있어 건너뛰었습니다/)).toBeInTheDocument();
    expect(screen.getByText("MES/mes.xlsx")).toBeInTheDocument();
    // 변경 없음 안내와는 구분되어야 한다.
    expect(screen.queryByText(/변경된 파일이 없어 건너뛰었습니다/)).not.toBeInTheDocument();
  });
});
