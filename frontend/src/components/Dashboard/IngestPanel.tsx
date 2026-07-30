import { useEffect, useState } from "react";
import { getIngestStatus, runIngest, type RunSummary } from "../../api/ingest";
import { useDashboardStore } from "../../store/dashboardStore";

// 손실 건수를 접어두지 않는다. API 에만 있으면 아무도 안 보고, 그러면
// "조용히 빠지는 데이터가 없어야 한다"는 원칙이 화면에서 끊긴다.
function Losses({ s }: { s: RunSummary }) {
  const items: string[] = [];
  if (s.orphan_mold_nos.length > 0) {
    items.push(
      `관리대장에 없는 금형 ${s.orphan_mold_nos.length}건: ${s.orphan_mold_nos.join(", ")}`
    );
  }
  // 관리대장은 시트 하나가 금형 하나다. 시트 이름이 JIG ID 로 안 읽히면 그
  // 금형이 통째로 빠지는데, 이름을 보여주지 않으면 어느 시트를 고쳐야 하는지
  // 알 수가 없다.
  if (s.bad_sheet_names.length > 0) {
    items.push(
      `관리대장 시트 이름을 JIG ID 로 읽지 못함 ${s.bad_sheet_names.length}건` +
        ` (해당 금형이 목록에서 빠짐): ${s.bad_sheet_names.join(", ")}`
    );
  }
  // 기준정보가 낡으면 그 금형이 MES 조회 키를 얻지 못해 목록에서 통째로
  // 빠진다. 가장 흔한 사고이므로 JIG ID 를 그대로 보여준다 — 그래야 사용자가
  // "아, 이 금형을 기준정보에 넣어야겠다"까지 갈 수 있다.
  if (s.unknown_jig_id.length > 0) {
    items.push(
      `JIG 기준정보에 없는 JIG ID ${s.unknown_jig_id.length}건` +
        ` (해당 금형이 목록에서 빠짐): ${s.unknown_jig_id.join(", ")}`
    );
  }
  // 설비명은 못 찾아도 금형은 나온다(JIG ID 로 폴백). 다만 그 구간의 실적을
  // "그때 그 설비"가 아니라 "현재 등록된 설비" 기준으로 읽었다는 뜻이라,
  // 위의 치명적 손실과 같은 무게로 읽히면 안 된다.
  if (s.unknown_equipment.length > 0) {
    items.push(
      `JIG 기준정보에 없는 설비 ${s.unknown_equipment.length}건` +
        ` (현재 등록된 설비의 실적으로 대체함): ${s.unknown_equipment.join(", ")}`
    );
  }
  // 값이 있어도 일부 날만 반영된 불량율이라는 사실이 드러나야 한다.
  if (s.missing_mes_days.length > 0) {
    items.push(
      `MES 파일이 없는 날 ${s.missing_mes_days.length}일` +
        ` (그날 실적이 불량율에서 빠짐): ${s.missing_mes_days.join(", ")}`
    );
  }
  if (s.unmatched_runs > 0) {
    items.push(`MES에서 실적을 찾지 못한 사용구간 ${s.unmatched_runs}건`);
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
        {/* 가동 중인 구간은 손실이 아니라 상태다. 경고에 섞으면 사용자가
            고칠 것이 없는데도 고치려 든다 — 그래서 요약 줄에 둔다. */}
        {s.open_runs > 0 && ` · 가동 중 ${s.open_runs}건(불량율 미확정)`}
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
