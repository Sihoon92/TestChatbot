import { useNavigate, useParams } from "react-router-dom";
import { useDashboardStore } from "../../store/dashboardStore";
import { STATUS_LABEL } from "../../types/mold";
import { fmtInstallation, fmtNumber, fmtPercent } from "./formatters";

// 선택 상태는 URL(:moldNo)이 진실이다. 스토어에 따로 두면 링크로 진입했을 때
// 두 값이 어긋난다.
export default function MoldList() {
  const molds = useDashboardStore((s) => s.molds);
  const listLoading = useDashboardStore((s) => s.listLoading);
  const listError = useDashboardStore((s) => s.listError);
  const resetFilters = useDashboardStore((s) => s.resetFilters);
  const navigate = useNavigate();
  const { moldNo } = useParams();

  if (listLoading && molds.length === 0) {
    return <p className="p-4 text-sm text-ink/60">불러오는 중…</p>;
  }

  // 목록이 비어 있는 이유가 "조건에 맞는 게 없어서"가 아니라 "불러오지
  // 못해서"일 때는 필터 초기화 버튼을 보여주지 않는다 — 문제였던 적 없는
  // 필터를 고치라고 유도하는 오판이 된다. 실패 사실 자체는 페이지 상단
  // 배너(DashboardPage)가 이미 알려준다.
  if (molds.length === 0 && listError !== null) {
    return <p className="p-4 text-sm text-ink/60">목록을 불러오지 못했습니다</p>;
  }

  if (molds.length === 0) {
    return (
      <div className="flex flex-col items-start gap-2 p-4">
        <p className="text-sm text-ink/60">조건에 맞는 금형이 없습니다</p>
        <button
          onClick={resetFilters}
          className="rounded-md bg-accent px-3 py-1.5 text-sm text-white hover:bg-accent-dark"
        >
          필터 초기화
        </button>
      </div>
    );
  }

  return (
    <ul className="flex flex-col gap-1 overflow-y-auto p-2">
      {molds.map((mold) => {
        const selected = mold.mold_no === moldNo;
        return (
          <li key={mold.mold_no}>
            <button
              onClick={() => navigate(`/dashboard/${encodeURIComponent(mold.mold_no)}`)}
              aria-current={selected ? "true" : undefined}
              className={`w-full rounded-md px-3 py-2 text-left text-sm hover:bg-paper-dark ${
                selected ? "bg-paper-dark font-medium" : ""
              }`}
            >
              <span className="block font-medium">{mold.mold_no}</span>
              <span className="block text-xs text-ink/60">
                {STATUS_LABEL[mold.status]} · {fmtInstallation(mold.line, mold.machine)} ·{" "}
                {fmtNumber(mold.shot_count)}타 · 불량 {fmtPercent(mold.latest_defect_rate)}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
