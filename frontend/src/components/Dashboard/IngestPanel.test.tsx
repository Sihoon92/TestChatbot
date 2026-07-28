import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import IngestPanel from "./IngestPanel";
import type { RunSummary } from "../../api/ingest";

vi.mock("../../api/ingest", async () => ({
  runIngest: vi.fn(),
  getIngestStatus: vi.fn(),
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
  skipped_rows: 0,
  files: ["MES/mes.xlsx"],
  error: null,
  unreadable_files: [],
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
