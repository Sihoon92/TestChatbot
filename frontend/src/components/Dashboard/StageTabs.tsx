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

  // 선택된 금형이 바뀌면 항상 생산결과 탭으로 되돌아가야 한다 — Task 11 은
  // 이 컴포넌트를 금형별로 다시 렌더링할 뿐 리마운트한다는 보장이 없다.
  // 렌더링 도중(이펙트가 아니라) 상태를 조정해, 잘못된 탭이 한 프레임이라도
  // 보이는 일이 없게 한다. React 공식 문서의 "prop 변경 시 state 조정" 패턴.
  const [shownMold, setShownMold] = useState(detail.summary.mold_no);
  if (shownMold !== detail.summary.mold_no) {
    setShownMold(detail.summary.mold_no);
    setActive("install");
  }

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
