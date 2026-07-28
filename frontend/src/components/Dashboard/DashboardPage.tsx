import { useEffect } from "react";
import { useParams } from "react-router-dom";
import { useDashboardStore } from "../../store/dashboardStore";
import IngestPanel from "./IngestPanel";
import MoldDetailPanel from "./MoldDetailPanel";
import MoldFilterBar from "./MoldFilterBar";
import MoldList from "./MoldList";

export default function DashboardPage() {
  const { moldNo } = useParams();
  const filters = useDashboardStore((s) => s.filters);
  const listError = useDashboardStore((s) => s.listError);
  const detailError = useDashboardStore((s) => s.detailError);
  const loadOptions = useDashboardStore((s) => s.loadOptions);
  const loadMolds = useDashboardStore((s) => s.loadMolds);
  const loadDetail = useDashboardStore((s) => s.loadDetail);
  const clearDetail = useDashboardStore((s) => s.clearDetail);

  // 필터 선택지는 한 번만 받으면 된다.
  useEffect(() => {
    void loadOptions();
  }, [loadOptions]);

  // 필터가 바뀔 때마다 목록을 다시 받는다. filters 객체는 setFilter 가 매번 새로
  // 만들므로(참조가 바뀌므로), 값이 실제로 바뀌었는지와 무관하게 setFilter 를
  // 호출할 때마다 이 효과가 다시 돈다.
  useEffect(() => {
    void loadMolds();
  }, [filters, loadMolds]);

  // 선택은 URL 이 진실이다. 주소창에 직접 입력하거나 링크로 진입해도 같은
  // 경로를 타도록, 목록 클릭이 아니라 moldNo 변화를 트리거로 삼는다.
  //
  // moldNo 가 바뀔 때마다(undefined 로 바뀔 때만이 아니라) 먼저 detail 을
  // 비운다 — 그러지 않으면 새 moldNo 로 loadDetail 이 도는 동안 화면에는
  // 이전 금형의 데이터가 그대로 남아, URL/목록의 선택 표시와 상세 패널이
  // 다른 금형을 가리키는 상태가 요청이 끝날 때까지 보인다.
  useEffect(() => {
    if (moldNo === undefined) {
      clearDetail();
      return;
    }
    clearDetail();
    void loadDetail(moldNo);
  }, [moldNo, loadDetail, clearDetail]);

  // listError 와 detailError 는 서로 다른 실패를 가리킨다(목록 화면 vs
  // 상세 화면). 하나가 다른 하나를 가리면 안 되므로 둘 다 있으면 둘 다
  // 보여준다 — 조용히 하나를 버리지 않는다.
  const errors = [listError, detailError].filter((e): e is string => e !== null);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <h1 className="sr-only">금형 관리</h1>

      {errors.length > 0 && (
        <div className="flex shrink-0 items-center justify-between gap-2 border-b border-accent bg-accent/10 px-3 py-2 text-sm">
          <span>불러오지 못했습니다: {errors.join(" / ")}</span>
          <button
            onClick={() => {
              // /filters(loadOptions), /molds(loadMolds), 상세(loadDetail)
              // 중 무엇이 실패했는지 배너만 봐서는 알 수 없다. 셋 다 다시
              // 시도해야, /filters 가 원인이었을 때도 재시도가 실제로
              // 문제를 고칠 수 있다.
              void loadOptions();
              void loadMolds();
              if (moldNo !== undefined) void loadDetail(moldNo);
            }}
            className="shrink-0 rounded-md bg-accent px-3 py-1 text-white hover:bg-accent-dark"
          >
            다시 시도
          </button>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        <aside className="flex w-72 shrink-0 flex-col border-r border-paper-dark">
          <IngestPanel />
          <MoldFilterBar />
          <MoldList />
        </aside>
        <main className="flex min-w-0 flex-1 flex-col overflow-y-auto">
          <MoldDetailPanel moldNo={moldNo} />
        </main>
      </div>
    </div>
  );
}
