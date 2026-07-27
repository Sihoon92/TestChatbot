import type { SourceRef, StagePanel } from "../../types/mold";
import { fmtText } from "./formatters";

// IQC / PQC / AI복검 세 탭이 이 컴포넌트 하나를 공유한다. 세 단계 모두
// StageItem[] 을 그리는 일이 같으므로, 단계가 늘어도 컴포넌트는 늘지 않는다.
// 이것이 유연 스키마를 택한 이유이기도 하다.

/** 추출된 값의 출처를 한 줄로. 값이 이상할 때 원본을 찾아갈 실마리가 된다. */
function sourceTitle(source: SourceRef | null): string | undefined {
  if (source === null) return undefined;
  return [source.file, source.sheet, source.cell].filter(Boolean).join(" · ");
}

export default function StageItemPanel({ panel }: { panel: StagePanel | undefined }) {
  if (panel === undefined || panel.status === "missing") {
    return <p className="p-4 text-sm text-ink/60">아직 문서가 없습니다</p>;
  }

  if (panel.status === "error") {
    return (
      <div className="p-4 text-sm">
        <p className="font-medium text-accent-dark">추출에 실패했습니다</p>
        <p className="mt-1 text-ink/70">{fmtText(panel.error)}</p>
      </div>
    );
  }

  if (panel.items.length === 0) {
    return <p className="p-4 text-sm text-ink/60">표시할 항목이 없습니다</p>;
  }

  return (
    <div className="p-4">
      {panel.updated_at !== null && (
        <p className="mb-2 text-xs text-ink/50">기준일 {panel.updated_at}</p>
      )}
      <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
        {/* label 은 자유 형식 AI 추출값이라 유일성이 보장되지 않는다(예: AI복검이
            IQC 와 같은 "경도"를 다시 검사). 게다가 IQC/PQC/AI복검 세 탭이 같은
            <StageItemPanel> 엘리먼트를 공유하므로 탭 전환은 리마운트가 아니라
            prop 갱신이다 — key 를 label 만으로 잡으면 서로 다른 단계의 항목이
            같은 key 로 충돌한다. 항목 순서도 의미가 있으므로(원본 파일 순서)
            index 를 key 의 일부로 쓰는 것이 정당하다. */}
        {panel.items.map((item, index) => (
          <div key={`${item.label}-${index}`} className="flex justify-between gap-2">
            <dt className="text-ink/60">{item.label}</dt>
            <dd className="flex items-center gap-1 font-medium">
              <span title={sourceTitle(item.source)}>{item.value}</span>
              {item.judgment === "ng" && (
                <span className="rounded bg-accent px-1 text-xs text-white">NG</span>
              )}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
