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

  // 설계(DesignSpec)와 생산결과(ProductionRun[])는 IQC/PQC/AI복검과 달리
  // StagePanel 스키마를 쓰지 않는다 — 그래서 StageItemPanel 같은 공용
  // 에러 분기가 없다. stage_status[stage] === "error" 인데도 두 탭을 그냥
  // 그리면, 설계 탭은 이유 없이 대시 여섯 개만 보여주고, 생산결과 탭은
  // runs가 빈 배열이라는 이유로 "생산 이력이 없습니다"라는 거짓 확언을
  // 하게 된다(추출이 실패했을 뿐 생산이 없었다는 근거는 없다).
  //
  // DesignSpec/ProductionRun 은 필드 단위 에러 사유를 실어 나르지 않는다
  // (backend/app/molds/schemas.py, frontend/src/types/mold.ts). 이유를
  // 지어내느니 "원인 정보 없음"이라고 정직하게 말하는 편이 낫다. 스키마를
  // 넓혀 IQC/PQC/AI복검처럼 사유 필드를 추가하는 일은 백엔드 계약 변경이
  // 필요해서, 계약이 검증된 상태로 고정된 이번 전체 리뷰 범위 밖으로
  // 미룬다 — 억지로 추측한 사유를 보여주는 것보다 나중에 제대로 하는 편이
  // 낫다는 판단이다.
  const activeExtractionFailed =
    (active === "design" || active === "install") && detail.summary.stage_status[active] === "error";

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
        {activeExtractionFailed ? (
          <div className="p-4 text-sm">
            <p className="font-medium text-accent-dark">추출에 실패했습니다</p>
            <p className="mt-1 text-ink/70">원인 정보가 없습니다</p>
          </div>
        ) : (
          <>
            {active === "install" && <ProductionTable runs={detail.productions} />}
            {active === "design" && <DesignPanel design={detail.design} />}
            {/* IQC / PQC / AI복검 — 한 컴포넌트가 세 탭을 처리한다 */}
            {active !== "install" && active !== "design" && <StageItemPanel panel={panel} />}
          </>
        )}
      </div>
    </div>
  );
}
