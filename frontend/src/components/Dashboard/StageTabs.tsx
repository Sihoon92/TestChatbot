import { useState } from "react";
import { STAGE_LABEL, TAB_ORDER, type MoldDetail, type StageKey, type StageStatus } from "../../types/mold";
import DesignPanel from "./DesignPanel";
import ProductionTable from "./ProductionTable";
import StageItemPanel from "./StageItemPanel";

// 탭 라벨 옆 표식. 어느 단계 데이터가 비었는지 탭을 열어보지 않고 알 수 있어야
// 한다 — AI 가 채우는 대시보드라 "값이 없다"가 정상 상태이기 때문이다.
function badge(status: StageStatus): string {
  if (status === "error") return " ⚠";
  if (status === "missing") return " ·";
  return "";
}

export default function StageTabs({ detail }: { detail: MoldDetail }) {
  const [active, setActive] = useState<StageKey>("install");
  const panel = detail.stages.find((s) => s.stage === active);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div role="tablist" className="flex shrink-0 gap-1 border-b border-paper-dark px-3">
        {TAB_ORDER.map((stage) => {
          const selected = stage === active;
          return (
            <button
              key={stage}
              role="tab"
              aria-selected={selected}
              onClick={() => setActive(stage)}
              className={`rounded-t-md px-3 py-1.5 text-sm ${
                selected ? "bg-white font-medium" : "text-ink/70 hover:bg-paper-dark"
              }`}
            >
              {STAGE_LABEL[stage]}
              {badge(detail.summary.stage_status[stage])}
            </button>
          );
        })}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {active === "install" && <ProductionTable runs={detail.productions} />}
        {active === "design" && <DesignPanel design={detail.design} />}
        {/* IQC / PQC / AI복검 — 한 컴포넌트가 세 탭을 처리한다 */}
        {active !== "install" && active !== "design" && <StageItemPanel panel={panel} />}
      </div>
    </div>
  );
}
