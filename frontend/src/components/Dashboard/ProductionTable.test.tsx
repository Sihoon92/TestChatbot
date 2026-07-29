import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ProductionRun } from "../../types/mold";
import ProductionTable, { defectLabels } from "./ProductionTable";

// 한 금형이 설비를 드나든 이력. 실물(RX39513)의 모양을 그대로 줄여 왔다.
const RUNS: ProductionRun[] = [
  {
    // 4일을 덮는 구간인데 MES 파일이 3일치뿐이다 — 일부만 반영된 불량율.
    install_seq: 1,
    line: "톈진 Pouch #10(S)",
    machine: "POU WND10_Stack(1차)_01",
    started_at: "2026-07-01T07:00:00",
    ended_at: "2026-07-05T07:00:00",
    grind_result: null,
    defect_rate: 0.012534,
    produced: 29439,
    defect_count: 369,
    days_covered: 3,
    days_expected: 4,
    defects: [{ label: "버", rate: 0.003 }],
  },
  {
    install_seq: 2,
    line: "톈진 Pouch #10(S)",
    machine: "POU WND10_Stack(1차)_01",
    started_at: "2026-07-06T09:00:00",
    ended_at: "2026-07-06T21:00:00",
    grind_result: null,
    defect_rate: 0.003359,
    produced: 8037,
    defect_count: 27,
    days_covered: 1,
    days_expected: 1,
    // rate: 0 은 "이 항목이 있었는데 발생률이 0" 이라는 실제 값이다.
    // Map.has() 대신 값의 truthy 여부로 판단하면 0 을 — 로 잘못 표시하게 된다.
    defects: [{ label: "버", rate: 0.001 }, { label: "크랙", rate: 0 }],
  },
  {
    // 아직 설비에 있다 — 종료도 불량율도 없다.
    install_seq: 3,
    line: null,
    machine: "POU WND10_Stack(1차)_01",
    started_at: "2026-07-14T09:00:00",
    ended_at: null,
    grind_result: null,
    defect_rate: null,
    produced: null,
    defect_count: null,
    days_covered: 0,
    days_expected: 0,
    defects: [],
  },
];

describe("defectLabels", () => {
  it("returns the union of labels across rows, in first-seen order", () => {
    // 정렬하면 원본 파일의 항목 순서 정보를 잃는다. 처음 등장한 순서를 지킨다.
    expect(defectLabels(RUNS)).toEqual(["버", "크랙"]);
  });

  it("returns an empty list when there are no runs", () => {
    expect(defectLabels([])).toEqual([]);
  });
});

describe("ProductionTable", () => {
  it("shows period, line, equipment and the quantities behind the rate", () => {
    // 불량율만 있으면 "이 1.253% 가 어디서 나왔나" 에 답할 수 없다.
    render(<ProductionTable runs={RUNS} />);
    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual([
      "기간", "라인", "설비", "일수", "투입", "불량", "불량율", "버", "크랙",
    ]);
  });

  it("marks a run whose rate covers only part of its days as 3/4일", () => {
    // 그냥 "3일" 로 쓰면 원래 3일짜리 구간과 구분되지 않아, 불완전한 불량율을
    // 완전한 값으로 오해하게 된다.
    render(<ProductionTable runs={RUNS} />);
    const row = screen.getByRole("row", { name: /^07-01/ });
    expect(within(row).getByText("3/4일")).toBeInTheDocument();
  });

  it("shows a complete run as a plain day count", () => {
    render(<ProductionTable runs={RUNS} />);
    const row = screen.getByRole("row", { name: /^07-06/ });
    expect(within(row).getByText("1일")).toBeInTheDocument();
  });

  it("shows an in-progress run as 가동 중 with no rate", () => {
    // 불량율이 빈 이유가 "아직 안 끝남" 인지 "조인 실패" 인지 구분돼야 한다.
    render(<ProductionTable runs={RUNS} />);
    const row = screen.getByRole("row", { name: /^07-14/ });
    expect(within(row).getByText("가동 중")).toBeInTheDocument();
    const cells = within(row).getAllByRole("cell");
    expect(cells[6]).toHaveTextContent("—"); // 불량율
  });

  it("renders the period without shifting by the browser timezone", () => {
    // naive ISO 문자열을 Date 로 파싱하면 한국에서 9시간이 밀려 07:00 이
    // 16:00 으로 보인다. 문자열을 자르므로 어느 타임존에서도 같아야 한다.
    render(<ProductionTable runs={RUNS} />);
    const row = screen.getByRole("row", { name: /^07-01/ });
    expect(
      within(row).getByText("07-01 07:00~07-05 07:00")
    ).toBeInTheDocument();
  });

  it("shows the rate to three decimals so runs can be compared", () => {
    // PPM 단위라 소수 첫째 자리로는 1.2%/1.3% 로 뭉개져 비교가 안 된다.
    render(<ProductionTable runs={RUNS} />);
    const row = screen.getByRole("row", { name: /^07-01/ });
    expect(within(row).getByText("1.253%")).toBeInTheDocument();
  });

  it("dashes a missing line instead of failing to render", () => {
    // 기준정보에 Line명이 없는 금형이 화면을 깨뜨리면 안 된다.
    render(<ProductionTable runs={RUNS} />);
    const row = screen.getByRole("row", { name: /^07-14/ });
    const cells = within(row).getAllByRole("cell");
    expect(cells[1]).toHaveTextContent("—"); // 라인
  });

  it("dashes a defect label that a given row does not have", () => {
    render(<ProductionTable runs={RUNS} />);
    const row = screen.getByRole("row", { name: /^07-01/ });
    // 이 구간에는 '크랙' 이 없다. 0.0% 로 채우면 "크랙 불량 없음" 이라는
    // 잘못된 정보가 된다. (헤더 순서상 8 = 크랙)
    const cells = within(row).getAllByRole("cell");
    expect(cells[8]).toHaveTextContent("—");
  });

  it("renders a genuine zero-rate defect as 0.0%, not a dash", () => {
    render(<ProductionTable runs={RUNS} />);
    const row = screen.getByRole("row", { name: /^07-06/ });
    // 이 구간의 '크랙' 은 rate: 0 으로 실제 기록된 값이다. "발생률 0%" 와
    // "집계되지 않음(—)" 은 다른 사실이므로 0 이 — 로 뭉개지면 안 된다.
    const cells = within(row).getAllByRole("cell");
    expect(cells[8]).toHaveTextContent("0.0%");
  });

  it("shows an empty-state message when there are no runs", () => {
    render(<ProductionTable runs={[]} />);
    expect(screen.getByText("생산 이력이 없습니다")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
