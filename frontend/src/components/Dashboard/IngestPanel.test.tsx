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
  unknown_jig_id: [],
  unknown_equipment: [],
  missing_mes_days: [],
  unmatched_runs: 0,
  open_runs: 0,
  bad_sheet_names: [],
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
      skipped_rows: 2,
    });

    render(<IngestPanel />);
    await userEvent.click(await screen.findByRole("button", { name: /수집 실행/ }));

    expect(await screen.findByText(/관리대장에 없는 금형 1건/)).toBeInTheDocument();
    expect(screen.getByText(/RX99999/)).toBeInTheDocument();
    expect(screen.getByText(/건너뛴 행 2건/)).toBeInTheDocument();
  });

  it("기준정보에 없는 JIG ID 를 원문 그대로 보여준다", async () => {
    // 기준정보가 낡으면 그 금형이 조회 키를 얻지 못해 목록에서 통째로 빠진다.
    // JIG ID 가 안 보이면 무엇을 표에 추가해야 하는지 알 수 없다.
    vi.mocked(api.runIngest).mockResolvedValue({
      ...OK,
      unknown_jig_id: ["RX77777"],
    });

    render(<IngestPanel />);
    await userEvent.click(await screen.findByRole("button", { name: /수집 실행/ }));

    expect(
      await screen.findByText(/JIG 기준정보에 없는 JIG ID 1건/)
    ).toBeInTheDocument();
    expect(screen.getByText(/RX77777/)).toBeInTheDocument();
  });

  it("시트 이름이 JIG ID 로 안 읽힌 관리대장 시트를 이름까지 보여준다", async () => {
    // 관리대장은 시트 하나가 금형 하나다. 이름이 깨지면 그 금형이 통째로
    // 빠지는데, 어느 시트인지 안 보이면 사용자가 고칠 수가 없다.
    vi.mocked(api.getIngestStatus).mockResolvedValue({
      ...OK,
      mold_count: 0,
      bad_sheet_names: ["합계"],
    });

    render(<IngestPanel />);

    expect(
      await screen.findByText(/관리대장 시트 이름을 JIG ID 로 읽지 못함 1건/)
    ).toBeInTheDocument();
    expect(await screen.findByText(/합계/)).toBeInTheDocument();
  });

  it("기준정보에 없는 설비는 금형이 빠졌다고 말하지 않는다", async () => {
    // 설비명을 못 찾아도 금형은 나온다(JIG ID 로 폴백). 치명적 손실과 같은
    // 문구로 띄우면 사용자가 없는 문제를 찾아 헤맨다.
    vi.mocked(api.runIngest).mockResolvedValue({
      ...OK,
      unknown_equipment: ["POU WND99_New_01"],
    });

    render(<IngestPanel />);
    await userEvent.click(await screen.findByRole("button", { name: /수집 실행/ }));

    expect(
      await screen.findByText(/현재 등록된 설비의 실적으로 대체함/)
    ).toBeInTheDocument();
    expect(screen.getByText(/POU WND99_New_01/)).toBeInTheDocument();
    expect(screen.queryByText(/목록에서 빠짐/)).not.toBeInTheDocument();
  });

  it("MES 파일이 빠진 날을 드러낸다", async () => {
    // 값이 있어도 일부 날만 반영된 불량율이라는 사실이 보여야 한다.
    vi.mocked(api.runIngest).mockResolvedValue({
      ...OK,
      missing_mes_days: ["2026-07-02", "2026-07-03"],
    });

    render(<IngestPanel />);
    await userEvent.click(await screen.findByRole("button", { name: /수집 실행/ }));

    expect(await screen.findByText(/MES 파일이 없는 날 2일/)).toBeInTheDocument();
  });

  it("가동 중인 구간은 경고가 아니라 요약에 둔다", async () => {
    // 고칠 것이 없는데 경고로 띄우면 사용자가 없는 문제를 찾아 헤맨다.
    vi.mocked(api.runIngest).mockResolvedValue({ ...OK, open_runs: 3 });

    render(<IngestPanel />);
    await userEvent.click(await screen.findByRole("button", { name: /수집 실행/ }));

    const line = await screen.findByText(/가동 중 3건/);
    expect(line).toBeInTheDocument();
    expect(line.textContent).toMatch(/금형 4건/);
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
