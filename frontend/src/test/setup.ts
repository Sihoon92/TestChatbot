import "@testing-library/jest-dom";
import { vi } from "vitest";

// jsdom 환경 보강: 일부 라이브러리가 참조하는 matchMedia shim.
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

// 테스트에서 실제 mermaid 엔진(무거운 ESM + 비동기 DOM 렌더)을 로드하지 않도록 전역 모킹.
vi.mock("mermaid", () => ({
  default: {
    initialize: vi.fn(),
    render: vi.fn(async () => ({ svg: '<svg data-testid="mermaid-svg"></svg>' })),
  },
}));
