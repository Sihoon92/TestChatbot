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

/** "2026-07-01T07:00:00" → "07-01 07:00". 값이 없으면 빈 문자열.
 *
 * `new Date()` 를 쓰지 않는다. 이벤트 시각은 타임존이 없는 naive ISO 문자열인데
 * Date 로 파싱하면 브라우저가 UTC 로 해석해 로컬 타임존만큼 밀린다 — 한국이면
 * 9시간이 어긋나 "07-01 07:00" 이 "07-01 16:00" 으로 보인다. 문자열을 자르는
 * 쪽이 결정적이고, 애초에 표시용이라 시간 계산이 필요 없다. */
function fmtStamp(v: string | null): string {
  if (v === null || v === "") return "";
  return `${v.slice(5, 10)} ${v.slice(11, 16)}`.trim();
}

/** 설비 사용구간을 "07-01 07:00~07-05 07:00" 으로. 진행 중이면 끝을 비운다. */
export function fmtPeriod(startedAt: string | null, endedAt: string | null): string {
  const start = fmtStamp(startedAt);
  if (start === "") return DASH;
  return `${start}~${fmtStamp(endedAt)}`;
}

/** 구간이 며칠치로 집계됐는지.
 *
 * 완전하면 "4일", MES 파일이 빠져 일부만 반영됐으면 "3/4일" 로 그 사실을
 * 드러낸다. 그냥 "3일" 로 쓰면 원래 3일짜리 구간과 구분되지 않아, 불완전한
 * 불량율을 완전한 값으로 오해하게 된다. */
export function fmtRunDays(
  covered: number | null,
  expected: number | null,
  endedAt: string | null
): string {
  if (endedAt === null) return "가동 중";
  if (expected === null || expected === 0) return DASH;
  return covered === expected ? `${expected}일` : `${covered ?? 0}/${expected}일`;
}

// A half-known dimension like 140×—mm is not a dimension; treat any partial pair as missing.
export function fmtMeasurePair(a: number | null, b: number | null, unit: string): string {
  return a === null || b === null ? DASH : `${a}×${b}${unit}`;
}
