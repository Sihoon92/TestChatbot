// 값이 없을 때의 표시는 반드시 이 기호다. null 을 0 이나 빈 문자열로 대체하면
// "사용 타수 0회(신품)"와 "사용 타수 미상(추출 실패)"이 화면에서 구분되지
// 않고, 현장에서 오판으로 이어진다. AI 가 채우는 대시보드라 값이 없는 것이
// 정상 상태이므로 이 구분이 특히 중요하다.
export const DASH = "—";

export function fmtNumber(v: number | null): string {
  return v === null ? DASH : v.toLocaleString("ko-KR");
}

export function fmtPercent(v: number | null, digits = 1): string {
  return v === null ? DASH : `${(v * 100).toFixed(digits)}%`;
}

export function fmtMeasure(v: number | null, unit: string): string {
  return v === null ? DASH : `${v}${unit}`;
}

export function fmtText(v: string | null): string {
  return v === null || v === "" ? DASH : v;
}

export function fmtInstallation(line: string | null, machine: string | null): string {
  return line !== null && machine !== null ? `${line}-${machine}` : DASH;
}
