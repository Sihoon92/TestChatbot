import { API_HEADERS } from "./headers";

const BASE = "/api";

// backend/app/ingest/schemas.py 의 RunSummary 와 같은 모양을 유지한다.
export interface RunSummary {
  status: "ok" | "error" | "skipped";
  started_at: string;
  finished_at: string | null;
  mold_count: number;
  iqc_matched: number;
  orphan_mold_nos: string[];
  unknown_statuses: string[];
  skipped_rows: number;
  files: string[];
  error: string | null;
  // 디스크에는 있는데 이번 회차에 읽지 못한 파일(대개 사람이 엑셀을 열어둔
  // 경우). 비어 있지 않으면 백엔드가 배치를 건너뛰고 status="skipped" 를
  // 돌려준다 — "변경 없어서 건너뜀"과 구분해서 보여줘야 한다.
  unreadable_files: string[];
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

export function runIngest(): Promise<RunSummary> {
  return fetch(`${BASE}/ingest/run`, {
    method: "POST",
    headers: API_HEADERS,
  }).then(json<RunSummary>);
}

export function getIngestStatus(): Promise<RunSummary | null> {
  return fetch(`${BASE}/ingest/status`, {
    method: "GET",
    headers: API_HEADERS,
  }).then(json<RunSummary | null>);
}
