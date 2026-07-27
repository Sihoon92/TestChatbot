import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getFilterOptions, getMold, listMolds } from "./molds";

describe("molds API client", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("[]", { status: 200 })),
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it("omits empty filters from the query string", async () => {
    await listMolds({ q: "", status: "all", line: null, machine: null });
    const url = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toBe("/api/molds");
  });

  it("sends only the filters that are set", async () => {
    await listMolds({ q: "M-10", status: "in_use", line: "3", machine: "2" });
    const url = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("status=in_use");
    expect(url).toContain("line=3");
    expect(url).toContain("machine=2");
    expect(url).toContain("q=M-10");
  });

  it("does not send status when it is 'all'", async () => {
    await listMolds({ q: "", status: "all", line: "3", machine: "2" });
    const url = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).not.toContain("status=");
    // 상태가 '전체'면 라인/호기 필터도 의미가 없다(종속 관계).
    expect(url).not.toContain("line=");
  });

  it("throws on HTTP error so callers can show the error banner", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("nope", { status: 500 })));
    await expect(listMolds({ q: "", status: "all", line: null, machine: null })).rejects.toThrow(
      "HTTP 500",
    );
  });

  it("requests the detail endpoint by mold number", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
    await getMold("M-1024");
    const url = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toBe("/api/molds/M-1024");
  });

  it("encodes mold numbers that contain URL-unsafe characters", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
    await getMold("M 10/24");
    const url = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toBe("/api/molds/M%2010%2F24");
  });

  it("requests the filter options endpoint", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
    await getFilterOptions();
    const url = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toBe("/api/molds/filters");
  });
});
