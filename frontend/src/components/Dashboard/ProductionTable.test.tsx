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
    ],
  },
];

describe("defectLabels", () => {
  it("returns the union of labels across rows, in first-seen order", () => {
    // 정렬하면 원본 파일의 항목 순서 정보를 잃는다. 처음 등장한 순서를 지킨다.
    expect(defectLabels(RUNS)).toEqual(["버", "크랙", "미성형"]);
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
      "설치#", "호기", "시간대", "연마결과", "불량율", "버", "크랙", "미성형",
    ]);
  });

  it("dashes a defect label that a given row does not have", () => {
    render(<ProductionTable runs={RUNS} />);
    const row = screen.getByRole("row", { name: /^37/ });
    // 37회차에는 '미성형'이 없다. 0.0% 로 채우면 "미성형 불량 없음"이라는
    // 잘못된 정보가 된다.
    expect(within(row).getAllByRole("cell").at(-1)).toHaveTextContent("—");
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
