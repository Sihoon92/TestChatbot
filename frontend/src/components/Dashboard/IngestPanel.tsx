import { useEffect, useState } from "react";
import { getIngestStatus, runIngest, type RunSummary } from "../../api/ingest";
import { useDashboardStore } from "../../store/dashboardStore";

// 손실 건수를 접어두지 않는다. API 에만 있으면 아무도 안 보고, 그러면
// "조용히 빠지는 데이터가 없어야 한다"는 원칙이 화면에서 끊긴다.
function Losses({ s }: { s: RunSummary }) {
  const items: string[] = [];
  if (s.orphan_mold_nos.length > 0) {
    items.push(
      `MES에 없는 금형 ${s.orphan_mold_nos.length}건: ${s.orphan_mold_nos.join(", ")}`
    );
  }
  if (s.unknown_statuses.length > 0) {
    // 원문만 보여주면 "그래서 몇 건이 사라졌는데?"에 답이 없다. 실물 어휘가
    // STATUS_MAP 밖이면 목록이 통째로 비는데, 건수가 보여야 사용자가
    // "아, STATUS_MAP 에 '가동'을 넣어야겠다"까지 갈 수 있다.
    const scale =
      s.unknown_status_rows > 0
        ? ` (이 상태의 행 ${s.unknown_status_rows}건이 제외됨)`
        : "";
    items.push(`인식하지 못한 상태: ${s.unknown_statuses.join(", ")}${scale}`);
  }
  if (s.skipped_rows > 0) items.push(`건너뛴 행 ${s.skipped_rows}건`);
  if (items.length === 0 && s.failed_files.length === 0) return null;

  return (
    <ul className="mt-1 space-y-0.5 text-xs text-accent-dark">
      {items.map((t) => (
        <li key={t}>⚠ {t}</li>
      ))}
      {/* 파싱에 실패한 IQC 파일. 배치는 성공(status="ok")이라 여기서 안
          보여주면 그 파일의 항목이 사유 없이 사라진다. 사유까지 그대로
          싣는 이유는 "어느 파일을 어떻게 고쳐야 하는가"가 곧 사유라서다. */}
      {s.failed_files.length > 0 && (
        <li>
          ⚠ 읽지 못한 파일 {s.failed_files.length}건
          <ul className="mt-0.5 space-y-0.5 pl-4">
            {s.failed_files.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        </li>
      )}
    </ul>
  );
}

// "변경 없어서 건너뜀"과 "파일을 못 읽어서 건너뜀"은 화면에서 구분돼야 한다.
// 후자는 사람이 엑셀을 닫아야 해결되는데, 한 문장으로 뭉치면 그 사실이 사라진다.
function Skipped({ s }: { s: RunSummary }) {
  if (s.unreadable_files.length > 0) {
    return (
      <div className="text-xs text-accent-dark">
        <p>⚠ 읽지 못한 파일이 있어 건너뛰었습니다. 파일을 닫고 다시 실행하세요.</p>
        <ul className="mt-0.5 space-y-0.5">
          {s.unreadable_files.map((f) => (
            <li key={f}>{f}</li>
          ))}
        </ul>
      </div>
    );
  }
  return <p className="text-xs text-ink/70">변경된 파일이 없어 건너뛰었습니다.</p>;
}

function Result({ s }: { s: RunSummary }) {
  if (s.status === "error") {
    return <p className="text-xs text-accent-dark">✕ 수집 실패: {s.error}</p>;
  }
  if (s.status === "skipped") {
    return <Skipped s={s} />;
  }
  return (
    <div>
      <p className="text-xs text-ink/70">
        금형 {s.mold_count}건 · IQC 붙은 금형 {s.iqc_matched}건
      </p>
      <Losses s={s} />
    </div>
  );
}

export default function IngestPanel() {
  const [summary, setSummary] = useState<RunSummary | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const loadMolds = useDashboardStore((s) => s.loadMolds);
  const loadOptions = useDashboardStore((s) => s.loadOptions);

  useEffect(() => {
    void getIngestStatus()
      .then(setSummary)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  async function onRun() {
    setRunning(true);
    setError(null);
    try {
      setSummary(await runIngest());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
      // 수집은 DB 를 통째로 갈아치운다. 목록을 다시 읽지 않으면 패널에는
      // "금형 120건"이 뜨는데 왼쪽 목록은 비어 있는 상태가 남는다
      // (DashboardPage 의 loadMolds 는 filters 변화에만 반응한다).
      // status 가 ok 일 때만이 아니라 항상 부른다 — skipped/error 여도
      // DB 의 현재 상태를 다시 확인하는 게 맞다.
      void loadMolds();
      void loadOptions();
    }
  }

  return (
    <div className="shrink-0 border-b border-paper-dark px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium">데이터 수집</span>
        <button
          onClick={() => void onRun()}
          disabled={running}
          className="rounded-md bg-accent px-2 py-1 text-xs text-white hover:bg-accent-dark disabled:opacity-60"
        >
          {running ? "수집 중…" : "수집 실행"}
        </button>
      </div>
      <div className="mt-1">
        {error && <p className="text-xs text-accent-dark">불러오지 못했습니다: {error}</p>}
        {!error && summary === null && (
          <p className="text-xs text-ink/70">아직 수집한 적이 없습니다.</p>
        )}
        {!error && summary !== null && <Result s={summary} />}
      </div>
    </div>
  );
}
