import { useDashboardStore } from "../../store/dashboardStore";
import StageTabs from "./StageTabs";
import SummaryCards from "./SummaryCards";

export default function MoldDetailPanel({ moldNo }: { moldNo: string | undefined }) {
  const detail = useDashboardStore((s) => s.detail);
  const detailLoading = useDashboardStore((s) => s.detailLoading);

  if (moldNo === undefined) {
    return (
      <p className="flex flex-1 items-center justify-center text-sm text-ink/60">
        왼쪽에서 금형을 선택하세요
      </p>
    );
  }

  if (detailLoading && detail === null) {
    return (
      <p className="flex flex-1 items-center justify-center text-sm text-ink/60">
        불러오는 중…
      </p>
    );
  }

  if (detail === null) {
    // 로딩도 아니고 데이터도 없다 = 조회 실패. 사유는 상단 배너가 보여준다.
    return (
      <p className="flex flex-1 items-center justify-center text-sm text-ink/60">
        금형 정보를 불러오지 못했습니다
      </p>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <h2 className="shrink-0 px-3 pt-3 text-base font-semibold">{detail.summary.mold_no}</h2>
      <SummaryCards detail={detail} />
      <StageTabs detail={detail} />
    </div>
  );
}
