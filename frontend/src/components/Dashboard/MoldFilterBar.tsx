import { useDashboardStore } from "../../store/dashboardStore";
import { STATUS_LABEL, type MoldStatus } from "../../types/mold";

// 전역 필터를 셋 나란히 두지 않는다. 금형 번호는 필터가 아니라 선택이므로
// 검색창으로, 설치 호기는 금형 상태에 종속이므로 상태가 '사용중'일 때만
// 노출한다(대기중 금형은 호기가 없어 "대기중 + 3-2"는 항상 0건이다).
const SELECT_CLASS =
  "rounded-md border border-paper-dark bg-white px-2 py-1.5 text-sm outline-none";

export default function MoldFilterBar() {
  const filters = useDashboardStore((s) => s.filters);
  const options = useDashboardStore((s) => s.options);
  const setFilter = useDashboardStore((s) => s.setFilter);

  const installations = options?.installations ?? [];
  const installationValue =
    filters.line && filters.machine ? `${filters.line}-${filters.machine}` : "";

  return (
    <div className="flex shrink-0 flex-col gap-2 border-b border-paper-dark p-3">
      <label className="flex flex-col gap-1 text-xs text-ink/70">
        금형 번호 검색
        <input
          type="search"
          value={filters.q}
          onChange={(e) => setFilter({ q: e.target.value })}
          placeholder="예: M-10"
          className="rounded-md border border-paper-dark bg-white px-2 py-1.5 text-sm outline-none"
        />
      </label>

      <div className="flex gap-2">
        <label className="flex flex-1 flex-col gap-1 text-xs text-ink/70">
          금형 상태
          <select
            value={filters.status}
            onChange={(e) => setFilter({ status: e.target.value as MoldStatus | "all" })}
            className={SELECT_CLASS}
          >
            {/* '전체'는 MoldStatus 값이 아니라 UI 전용 선택지다. 선택 시
                status 쿼리 파라미터를 아예 보내지 않는다. */}
            <option value="all">전체</option>
            {(options?.statuses ?? []).map((s) => (
              <option key={s} value={s}>
                {STATUS_LABEL[s]}
              </option>
            ))}
          </select>
        </label>

        {filters.status === "in_use" && (
          <label className="flex flex-1 flex-col gap-1 text-xs text-ink/70">
            라인/호기
            <select
              value={installationValue}
              onChange={(e) => {
                const [line, machine] = e.target.value.split("-");
                setFilter({ line: line || null, machine: machine || null });
              }}
              className={SELECT_CLASS}
            >
              <option value="">전체</option>
              {installations.map(({ line, machine }) => (
                <option key={`${line}-${machine}`} value={`${line}-${machine}`}>
                  {line}-{machine}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
    </div>
  );
}
