import { useDashboardStore } from "../../store/dashboardStore";
import StageTabs from "./StageTabs";
import SummaryCards from "./SummaryCards";

export default function MoldDetailPanel({ moldNo }: { moldNo: string | undefined }) {
  const detail = useDashboardStore((s) => s.detail);
  const error = useDashboardStore((s) => s.error);

  if (moldNo === undefined) {
    return (
      <p className="flex flex-1 items-center justify-center text-sm text-ink/60">
        왼쪽에서 금형을 선택하세요
      </p>
    );
  }

  // detail 이 있으면 그걸 보여준다. DashboardPage 의 moldNo 이펙트가 매 선택마다
  // clearDetail() 을 먼저 호출하므로, 여기 남은 detail 은 항상 현재 moldNo 의
  // 것이다 — 옛 금형의 데이터가 새 금형인 척 보일 일이 없다.
  if (detail !== null) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <h2 className="shrink-0 px-3 pt-3 text-base font-semibold">{detail.summary.mold_no}</h2>
        <SummaryCards detail={detail} />
        <StageTabs detail={detail} />
      </div>
    );
  }

  if (error !== null) {
    return (
      <p className="flex flex-1 items-center justify-center text-sm text-ink/60">
        금형 정보를 불러오지 못했습니다
      </p>
    );
  }

  // 이 아래는 "요청이 실행 중"이거나 "moldNo 는 정해졌지만 이펙트가 아직 돌지
  // 않은 첫 렌더"뿐이다. 둘 다 아직 실패한 게 아니므로 로딩 문구가 정직한
  // 기본값이다 — 시작조차 안 한 요청을 실패로 보여주면 안 된다.
  return (
    <p className="flex flex-1 items-center justify-center text-sm text-ink/60">
      불러오는 중…
    </p>
  );
}
