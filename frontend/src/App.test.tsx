import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AppRoutes } from "./App";

// 대시보드는 마운트 시 데이터를 가져온다. 이 테스트의 관심사는 라우팅뿐이므로
// 네트워크를 타지 않도록 API 모듈을 통째로 모킹한다.
vi.mock("./api/molds", () => ({
  listMolds: vi.fn(async () => []),
  getMold: vi.fn(async () => null),
  getFilterOptions: vi.fn(async () => ({ statuses: [], installations: [] })),
}));

// 채팅 화면도 마운트 시 세션 목록을 가져온다. 같은 이유로 모킹한다.
vi.mock("./api/client", () => ({
  listSessions: vi.fn(async () => []),
  createSession: vi.fn(),
  renameSession: vi.fn(),
  deleteSession: vi.fn(),
  getMessages: vi.fn(async () => ({ messages: [] })),
  checkLlm: vi.fn(async () => ({ ok: true, models: [], error: null })),
}));

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>,
  );
}

describe("app routing", () => {
  it("redirects the root path to the chat screen", () => {
    renderAt("/");
    expect(screen.getByRole("button", { name: "+ 새 세션" })).toBeInTheDocument();
  });

  it("renders the dashboard at /dashboard", () => {
    renderAt("/dashboard");
    expect(screen.getByRole("heading", { name: "금형 관리" })).toBeInTheDocument();
  });

  it("shows both nav links on every screen", () => {
    renderAt("/dashboard");
    expect(screen.getByRole("link", { name: "채팅" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "대시보드" })).toBeInTheDocument();
  });

  it("navigates from chat to dashboard via the header link", async () => {
    renderAt("/chat");
    await userEvent.click(screen.getByRole("link", { name: "대시보드" }));
    expect(screen.getByRole("heading", { name: "금형 관리" })).toBeInTheDocument();
  });

  it("marks the active screen with aria-current", () => {
    renderAt("/dashboard");
    expect(screen.getByRole("link", { name: "대시보드" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "채팅" })).not.toHaveAttribute("aria-current");
  });
});
