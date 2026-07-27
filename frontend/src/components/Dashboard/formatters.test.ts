import { describe, expect, it } from "vitest";
import { DASH, fmtInstallation, fmtMeasure, fmtMeasurePair, fmtNumber, fmtPercent, fmtText } from "./formatters";

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
