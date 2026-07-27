import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ProductionRun } from "../../types/mold";
import ProductionTable, { defectLabels } from "./ProductionTable";

const RUNS: ProductionRun[] = [
  {
    install_seq: 37, line: "3", machine: "2",
    started_at: "2026-07-20", ended_at: null,
    grind_result: "OK", defect_rate: 0.008,
    defects: [{ label: "버", rate: 0.003 }, { label: "크랙", rate: 0.002 }],
  },
  {
    install_seq: 36, line: "1", machine: "4",
    started_at: "2026-07-10", ended_at: "2026-07-19",
    grind_result: "재연마", defect_rate: 0.021,
    defects: [
      { label: "버", rate: 0.012 },
      { label: "크랙", rate: 0.005 },
      { label: "미성형", rate: 0.004 },
      // rate: 0 은 "이 항목이 있었는데 발생률이 0" 이라는 실제 값이다.
      // Map.has() 대신 값의 truthy 여부로 판단하면 0 을 — 로 잘못 표시하게 된다.
      { label: "스크래치", rate: 0 },
    ],
  },
];

describe("defectLabels", () => {
  it("returns the union of labels across rows, in first-seen order", () => {
    // 정렬하면 원본 파일의 항목 순서 정보를 잃는다. 처음 등장한 순서를 지킨다.
    expect(defectLabels(RUNS)).toEqual(["버", "크랙", "미성형", "스크래치"]);
  });

  it("returns an empty list when there are no runs", () => {
    expect(defectLabels([])).toEqual([]);
  });
});

describe("ProductionTable", () => {
  it("creates one column per defect label found in the data", () => {
    render(<ProductionTable runs={RUNS} />);
    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual([
      "설치#", "호기", "시간대", "연마결과", "불량율", "버", "크랙", "미성형", "스크래치",
    ]);
  });

  it("dashes a defect label that a given row does not have", () => {
    render(<ProductionTable runs={RUNS} />);
    const row = screen.getByRole("row", { name: /^37/ });
    // 37회차에는 '미성형'이 없다. 0.0% 로 채우면 "미성형 불량 없음"이라는
    // 잘못된 정보가 된다. 뒤에 새 컬럼이 추가될 수 있어 .at(-1) 대신 헤더
    // 순서(설치#,호기,시간대,연마결과,불량율,버,크랙,미성형,스크래치)와
    // 맞춘 인덱스(7 = 미성형)로 특정 컬럼을 명시적으로 지정한다.
    const cells = within(row).getAllByRole("cell");
    expect(cells[7]).toHaveTextContent("—");
  });

  it("renders a genuine zero-rate defect as 0.0%, not a dash", () => {
    render(<ProductionTable runs={RUNS} />);
    const row = screen.getByRole("row", { name: /^36/ });
    // 36회차의 '스크래치'는 rate: 0 으로 실제 기록된 값이다(8 = 스크래치 컬럼).
    // "발생률 0%" 와 "집계되지 않음(—)" 은 다른 사실이므로 0 이 —로
    // 뭉개지면 안 된다.
    const cells = within(row).getAllByRole("cell");
    expect(cells[8]).toHaveTextContent("0.0%");
  });

  it("shows an in-progress run with a dash for the end date", () => {
    render(<ProductionTable runs={RUNS} />);
    const row = screen.getByRole("row", { name: /^37/ });
    expect(within(row).getByText("2026-07-20 ~ —")).toBeInTheDocument();
  });

  it("renders the installation as line-machine", () => {
    render(<ProductionTable runs={RUNS} />);
    const row = screen.getByRole("row", { name: /^37/ });
    expect(within(row).getByText("3-2")).toBeInTheDocument();
  });

  it("shows an empty-state message when there are no runs", () => {
    render(<ProductionTable runs={[]} />);
    expect(screen.getByText("생산 이력이 없습니다")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
