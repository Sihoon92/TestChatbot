import { Link } from "react-router-dom";
import { useDashboardStore } from "../../store/dashboardStore";
import StageTabs from "./StageTabs";
import SummaryCards from "./SummaryCards";

// API 클라이언트(frontend/src/api/molds.ts)는 상태 코드를 따로 실어 나르지
// 않고 new Error(`HTTP ${res.status}`) 문자열만 던진다. 그래서 404 여부를
// 문자열 비교로 확인할 수밖에 없다 — 에러 처리가 더 복잡해지면(예: 403 도
// 구분해야 한다면) 클라이언트가 상태 코드를 직접 실어 나르도록 바꾸는 게
// 맞다. 지금은 종류가 하나뿐이라 이 정도 임시방편으로 충분하다.
function isNotFoundError(errorMessage: string): boolean {
  return errorMessage === "HTTP 404";
}

export default function MoldDetailPanel({ moldNo }: { moldNo: string | undefined }) {
  const detail = useDashboardStore((s) => s.detail);
  const detailLoading = useDashboardStore((s) => s.detailLoading);
  const detailError = useDashboardStore((s) => s.detailError);

  if (moldNo === undefined) {
    return (
      <p className="flex flex-1 items-center justify-center text-sm text-ink/60">
        왼쪽에서 금형을 선택하세요
      </p>
    );
  }

  // detailLoading 을 detailError 보다 먼저 확인한다. moldNo 가 바뀌면
  // DashboardPage 가 clearDetail() 직후 loadDetail() 을 부르고, loadDetail
  // 은 await 전에 곧바로 detailLoading: true 를 쓴다 — 옛 금형의
  // detailError 가 아직 스토어에 남아 있더라도, 새 요청이 도는 동안은
  // 로딩 문구가 맞다.
  if (detailLoading) {
    return (
      <p className="flex flex-1 items-center justify-center text-sm text-ink/60">
        불러오는 중…
      </p>
    );
  }

  if (detailError !== null) {
    if (isNotFoundError(detailError)) {
      return (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 text-sm text-ink/60">
          <p>{moldNo} 은(는) 존재하지 않는 금형 번호입니다</p>
          <Link
            to="/dashboard"
            className="rounded-md bg-accent px-3 py-1.5 text-sm text-white hover:bg-accent-dark"
          >
            목록으로 돌아가기
          </Link>
        </div>
      );
    }
    return (
      <p className="flex flex-1 items-center justify-center text-sm text-ink/60">
        금형 정보를 불러오지 못했습니다
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

  // 이 아래는 "moldNo 는 정해졌지만 이펙트가 아직 돌지 않은 첫 렌더"뿐이다
  // (로딩도 에러도 아직 시작 전). 아직 실패한 게 아니므로 로딩 문구가
  // 정직한 기본값이다 — 시작조차 안 한 요청을 실패로 보여주면 안 된다.
  return (
    <p className="flex flex-1 items-center justify-center text-sm text-ink/60">
      불러오는 중…
    </p>
  );
}
