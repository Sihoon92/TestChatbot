// 금형 대시보드 데이터 계약(프론트 쪽 정의).
// backend/app/molds/schemas.py 와 같은 모양을 유지해야 한다 — 한쪽만 고치면
// 화면과 API 가 조용히 어긋난다. 필드를 바꿀 때는 반드시 양쪽을 함께 고친다.

export type MoldStatus = "in_use" | "standby" | "repair" | "retired";
export type StageKey = "design" | "iqc" | "pqc" | "install" | "ai_recheck";
export type StageStatus = "ok" | "missing" | "error";

export interface SourceRef {
  file: string;
  sheet: string | null;
  cell: string | null;
}

export interface StageItem {
  label: string;
  value: string;
  judgment: "ok" | "ng" | null;
  source: SourceRef | null;
}

export interface StagePanel {
  stage: StageKey;
  status: StageStatus;
  updated_at: string | null;
  error: string | null;
  items: StageItem[];
}

export interface DesignSpec {
  angle_deg: number | null;
  height_mm: number | null;
  step_mm: number | null;
  overall_mm: number | null;
  plate_height_mm: number | null;
  plate_width_mm: number | null;
}

export interface CumulativeHistory {
  // null = 미상. 0(신품)과 구분한다 — 화면은 null 을 `—` 로 그린다.
  total_installs: number | null;
  total_production: number | null;
  first_installed_at: string | null;
}

export interface CurrentState {
  status: MoldStatus;
  line: string | null;
  machine: string | null;
  shot_count: number | null;
  installed_at: string | null;
}

export interface DefectRate {
  label: string;
  rate: number;
}

export interface ProductionRun {
  install_seq: number;
  line: string;
  machine: string;
  started_at: string;
  ended_at: string | null;
  grind_result: string | null;
  defect_rate: number | null;
  defects: DefectRate[];
}

export interface MoldSummary {
  mold_no: string;
  status: MoldStatus;
  line: string | null;
  machine: string | null;
  shot_count: number | null;
  latest_defect_rate: number | null;
  total_production: number | null;
  stage_status: Record<StageKey, StageStatus>;
}

export interface MoldDetail {
  summary: MoldSummary;
  design: DesignSpec;
  history: CumulativeHistory;
  current: CurrentState;
  productions: ProductionRun[];
  stages: StagePanel[];
}

export interface Installation {
  line: string;
  machine: string;
}

export interface FilterOptions {
  statuses: MoldStatus[];
  installations: Installation[];
}

// 화면의 필터 상태. "all" 은 MoldStatus 가 아니라 UI 전용 선택지이며,
// 선택 시 status 쿼리 파라미터를 보내지 않는다.
export interface MoldFilters {
  q: string;
  status: MoldStatus | "all";
  line: string | null;
  machine: string | null;
}

export const STATUS_LABEL: Record<MoldStatus, string> = {
  in_use: "사용중",
  standby: "대기중",
  repair: "수리중",
  retired: "폐기",
};

export const STAGE_LABEL: Record<StageKey, string> = {
  install: "생산결과",
  design: "설계",
  iqc: "IQC",
  pqc: "PQC",
  ai_recheck: "AI복검",
};

// 탭 표시 순서. 가장 자주 보는 생산결과를 맨 앞에 둔다.
export const TAB_ORDER: StageKey[] = ["install", "design", "iqc", "pqc", "ai_recheck"];
