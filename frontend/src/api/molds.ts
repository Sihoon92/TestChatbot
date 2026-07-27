import type { FilterOptions, MoldDetail, MoldFilters, MoldSummary } from "../types/mold";
import { API_HEADERS } from "./headers";

const BASE = "/api";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

/** 값이 설정된 필터만 쿼리스트링에 담는다. 빈 값을 보내면 서버가 ""로 필터해 0건이 된다. */
function toQuery(filters: MoldFilters): string {
  const params = new URLSearchParams();
  const q = filters.q.trim();
  if (q) params.set("q", q);
  if (filters.status !== "all") {
    params.set("status", filters.status);
    // 라인/호기는 상태에 종속이다. 상태가 '전체'면 이 값들은 의미가 없으므로
    // 보내지 않는다 — 화면에서 드롭다운이 사라져 있어도 스토어에 옛 값이
    // 남아 있을 수 있어, 여기서 한 번 더 막는다.
    if (filters.line) params.set("line", filters.line);
    if (filters.machine) params.set("machine", filters.machine);
  }
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export function listMolds(filters: MoldFilters): Promise<MoldSummary[]> {
  return fetch(`${BASE}/molds${toQuery(filters)}`, {
    method: "GET",
    headers: API_HEADERS,
  }).then(json<MoldSummary[]>);
}

export function getMold(moldNo: string): Promise<MoldDetail> {
  return fetch(`${BASE}/molds/${encodeURIComponent(moldNo)}`, {
    method: "GET",
    headers: API_HEADERS,
  }).then(json<MoldDetail>);
}

export function getFilterOptions(): Promise<FilterOptions> {
  return fetch(`${BASE}/molds/filters`, {
    method: "GET",
    headers: API_HEADERS,
  }).then(json<FilterOptions>);
}
