import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { MoldDetail } from "../../types/mold";
import StageTabs from "./StageTabs";

const DETAIL: MoldDetail = {
  summary: {
    mold_no: "M-0998", status: "standby", line: null, machine: null,
    shot_count: 0, latest_defect_rate: null, total_production: 2811300,
    stage_status: { design: "ok", iqc: "ok", pqc: "error", install: "ok", ai_recheck: "missing" },
  },
  design: {
    angle_deg: null, height_mm: 52, step_mm: null,
    overall_mm: 240, plate_height_mm: 140, plate_width_mm: null,
  },
  history: { total_installs: 64, total_production: 2811300, first_installed_at: "2021-02-15" },
  current: { status: "standby", line: null, machine: null, shot_count: 0, installed_at: null },
  productions: [
    {
      install_seq: 64, line: "1", machine: "4",
      started_at: "2026-05-02T08:00:00", ended_at: "2026-06-30T08:00:00",
      grind_result: "OK", defect_rate: 0.005,
      produced: 120000, defect_count: 600,
      days_covered: 59, days_expected: 59,
      defects: [{ label: "버", rate: 0.005 }],
    },
  ],
  stages: [
    {
      stage: "iqc", status: "ok", updated_at: "2021-02-14", error: null,
      items: [
        {
          label: "경도", value: "HRC 60", judgment: "ok",
          source: { file: "IQC/2021-02-14_M-0998.xlsx", sheet: "검사", cell: "C12" },
        },
      ],
    },
    {
      stage: "pqc", status: "error", updated_at: null,
      error: "시트 '공정'을 찾지 못했습니다", items: [],
    },
    { stage: "ai_recheck", status: "missing", updated_at: null, error: null, items: [] },
  ],
};

describe("StageTabs", () => {
  it("renders all five tabs in display order", () => {
    render(<StageTabs detail={DETAIL} />);
    const tabs = screen.getAllByRole("tab").map((t) => t.textContent);
    expect(tabs).toEqual([
      "생산결과", "설계", "IQC", "PQC ⚠", "AI복검 ·",
    ]);
  });

  it("opens on the production tab", () => {
    render(<StageTabs detail={DETAIL} />);
    expect(screen.getByRole("tab", { name: "생산결과" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("switches to the design tab on click", async () => {
    render(<StageTabs detail={DETAIL} />);
    await userEvent.click(screen.getByRole("tab", { name: "설계" }));
    expect(screen.getByText("각도")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows the failure reason on a stage whose extraction errored", async () => {
    render(<StageTabs detail={DETAIL} />);
    await userEvent.click(screen.getByRole("tab", { name: "PQC ⚠" }));
    expect(screen.getByText(/시트 '공정'을 찾지 못했습니다/)).toBeInTheDocument();
  });

  it("shows a missing-document message on a stage with no data", async () => {
    render(<StageTabs detail={DETAIL} />);
    await userEvent.click(screen.getByRole("tab", { name: "AI복검 ·" }));
    expect(screen.getByText("아직 문서가 없습니다")).toBeInTheDocument();
  });

  it("shows the source reference as a tooltip on an extracted value", async () => {
    render(<StageTabs detail={DETAIL} />);
    await userEvent.click(screen.getByRole("tab", { name: "IQC" }));
    expect(screen.getByText("HRC 60")).toHaveAttribute(
      "title",
      "IQC/2021-02-14_M-0998.xlsx · 검사 · C12",
    );
  });

  it("renders both values when two items in the same stage share a label", async () => {
    // label 은 유일성이 보장되지 않는다 — AI복검이 IQC 와 같은 항목을 다시
    // 검사하는 경우가 대표적이다. 이 테스트가 없으면 key 충돌로 한쪽 값이
    // 사라지는 회귀를 잡지 못한다.
    const detailWithDuplicateLabels: MoldDetail = {
      ...DETAIL,
      stages: [
        {
          stage: "iqc", status: "ok", updated_at: "2021-02-14", error: null,
          items: [
            { label: "경도", value: "HRC 60", judgment: "ok", source: null },
            { label: "경도", value: "HRC 62", judgment: "ok", source: null },
          ],
        },
        ...DETAIL.stages.filter((s) => s.stage !== "iqc"),
      ],
    };
    render(<StageTabs detail={detailWithDuplicateLabels} />);
    await userEvent.click(screen.getByRole("tab", { name: "IQC" }));
    expect(screen.getByText("HRC 60")).toBeInTheDocument();
    expect(screen.getByText("HRC 62")).toBeInTheDocument();
  });

  // 전체 리뷰 finding: 생산결과/설계 탭은 StagePanel 이 아니라 전용 스키마를
  // 써서 StageItemPanel 의 에러 분기를 타지 않는다. stage_status 가
  // "error"인데도 그냥 그리면, 생산결과 탭은 추출 실패를 "생산 이력이
  // 없습니다"라는 거짓 확언으로 보여준다.
  it('does not claim "생산 이력이 없습니다" when the install stage extraction errored', () => {
    const detailWithInstallError: MoldDetail = {
      ...DETAIL,
      summary: { ...DETAIL.summary, stage_status: { ...DETAIL.summary.stage_status, install: "error" } },
      productions: [],
    };
    render(<StageTabs detail={detailWithInstallError} />);
    expect(screen.queryByText("생산 이력이 없습니다")).not.toBeInTheDocument();
    expect(screen.getByText("추출에 실패했습니다")).toBeInTheDocument();
  });

  it("shows a failure message on the design tab when its extraction errored", async () => {
    const detailWithDesignError: MoldDetail = {
      ...DETAIL,
      summary: { ...DETAIL.summary, stage_status: { ...DETAIL.summary.stage_status, design: "error" } },
    };
    render(<StageTabs detail={detailWithDesignError} />);
    await userEvent.click(screen.getByRole("tab", { name: /설계/ }));
    expect(screen.getByText("추출에 실패했습니다")).toBeInTheDocument();
    expect(screen.queryByText("각도")).not.toBeInTheDocument();
  });

  it("resets to the production tab when a different mold is shown, but keeps the tab for the same mold", async () => {
    const { rerender } = render(<StageTabs detail={DETAIL} />);
    await userEvent.click(screen.getByRole("tab", { name: "PQC ⚠" }));
    expect(screen.getByRole("tab", { name: "PQC ⚠" })).toHaveAttribute("aria-selected", "true");

    const OTHER_DETAIL: MoldDetail = {
      ...DETAIL,
      summary: { ...DETAIL.summary, mold_no: "M-0001" },
    };
    rerender(<StageTabs detail={OTHER_DETAIL} />);
    expect(screen.getByRole("tab", { name: "생산결과" })).toHaveAttribute("aria-selected", "true");

    await userEvent.click(screen.getByRole("tab", { name: "PQC ⚠" }));
    // Same mold_no, but a fresh object — proves the reset keys off mold identity
    // (mold_no), not object reference or "every rerender".
    rerender(<StageTabs detail={{ ...OTHER_DETAIL }} />);
    expect(screen.getByRole("tab", { name: "PQC ⚠" })).toHaveAttribute("aria-selected", "true");
  });
});
