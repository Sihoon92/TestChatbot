import { describe, expect, it } from "vitest";
import {
  DASH,
  fmtInstallation,
  fmtMeasure,
  fmtMeasurePair,
  fmtNumber,
  fmtPercent,
  fmtPeriod,
  fmtRunDays,
  fmtText,
} from "./formatters";

describe("formatters", () => {
  it("renders null as a dash, never as zero", () => {
    expect(fmtNumber(null)).toBe(DASH);
    expect(fmtPercent(null)).toBe(DASH);
    expect(fmtMeasure(null, "mm")).toBe(DASH);
    expect(fmtText(null)).toBe(DASH);
  });

  it("keeps a real zero distinguishable from null", () => {
    // "타수 0회(신품)"와 "타수 미상(추출 실패)"이 화면에서 구분되어야 한다.
    expect(fmtNumber(0)).toBe("0");
    expect(fmtPercent(0)).toBe("0.0%");
    expect(fmtMeasure(0, "mm")).toBe("0mm");
  });

  it("groups thousands for readability", () => {
    expect(fmtNumber(1204500)).toBe("1,204,500");
  });

  it("renders a rate as a percentage with one decimal by default", () => {
    expect(fmtPercent(0.008)).toBe("0.8%");
    expect(fmtPercent(0.0215, 2)).toBe("2.15%");
  });

  it("appends the unit to a measurement", () => {
    expect(fmtMeasure(12.5, "°")).toBe("12.5°");
    expect(fmtMeasure(45, "mm")).toBe("45mm");
  });

  it("treats an empty string as missing", () => {
    expect(fmtText("")).toBe(DASH);
    expect(fmtText("OK")).toBe("OK");
  });

  it("joins line and machine, and dashes when either is missing", () => {
    expect(fmtInstallation("3", "2")).toBe("3-2");
    expect(fmtInstallation(null, null)).toBe(DASH);
    expect(fmtInstallation("3", null)).toBe(DASH);
  });

  it("formats a pair of dimensions, dashing if either is null", () => {
    expect(fmtMeasurePair(140, 80, "mm")).toBe("140×80mm");
    expect(fmtMeasurePair(null, 80, "mm")).toBe(DASH);
    expect(fmtMeasurePair(140, null, "mm")).toBe(DASH);
    expect(fmtMeasurePair(null, null, "mm")).toBe(DASH);
  });

  it("keeps a real zero pair distinguishable from null in measurements", () => {
    expect(fmtMeasurePair(0, 0, "mm")).toBe("0×0mm");
    expect(fmtMeasurePair(0, null, "mm")).toBe(DASH);
  });
});

describe("fmtPeriod", () => {
  it("renders a closed period as start~end", () => {
    expect(fmtPeriod("2026-07-01T07:00:00", "2026-07-05T07:00:00")).toBe(
      "07-01 07:00~07-05 07:00"
    );
  });

  it("leaves the end open for a run still in progress", () => {
    expect(fmtPeriod("2026-07-14T09:00:00", null)).toBe("07-14 09:00~");
  });

  it("does not shift by the browser timezone", () => {
    // naive ISO 문자열을 Date 로 파싱하면 한국에서 9시간이 밀려 07:00 이
    // 16:00 으로 보인다. 문자열을 자르므로 어느 타임존에서도 같아야 한다.
    expect(fmtPeriod("2026-07-01T00:30:00", "2026-07-01T23:30:00")).toBe(
      "07-01 00:30~07-01 23:30"
    );
  });

  it("dashes when the start is unknown", () => {
    expect(fmtPeriod(null, "2026-07-05T07:00:00")).toBe(DASH);
  });
});

describe("fmtRunDays", () => {
  it("shows a plain count when every day was covered", () => {
    expect(fmtRunDays(4, 4, "2026-07-05T07:00:00")).toBe("4일");
  });

  it("shows covered/expected when MES files were missing", () => {
    // "3일" 로 쓰면 원래 3일짜리 구간과 구분되지 않아, 일부만 반영된
    // 불량율을 완전한 값으로 오해하게 된다.
    expect(fmtRunDays(3, 4, "2026-07-05T07:00:00")).toBe("3/4일");
  });

  it("reports an unfinished run as 가동 중, not as zero days", () => {
    // 불량율이 빈 이유가 "아직 안 끝남" 인지 "조인 실패" 인지 구분돼야 한다.
    expect(fmtRunDays(0, 0, null)).toBe("가동 중");
  });

  it("dashes when the expected span is unknown", () => {
    expect(fmtRunDays(null, null, "2026-07-05T07:00:00")).toBe(DASH);
  });
});
