import { NavLink } from "react-router-dom";

// 두 화면 위에 고정되는 얇은 전환 바. 대시보드에는 채팅의 Sidebar 가 없어
// 버튼을 넣을 자리가 없고, 화면마다 따로 두면 위치가 어긋나므로 공통으로 둔다.
const linkClass = ({ isActive }: { isActive: boolean }) =>
  `rounded-md px-3 py-1.5 text-sm ${
    isActive ? "bg-accent text-white" : "text-ink hover:bg-paper-dark"
  }`;

export default function AppHeader() {
  return (
    <header className="flex shrink-0 items-center gap-3 border-b border-paper-dark bg-paper px-4 py-2">
      <span className="text-sm font-semibold">금형관리</span>
      <nav className="flex gap-1">
        <NavLink to="/chat" className={linkClass}>
          채팅
        </NavLink>
        <NavLink to="/dashboard" className={linkClass}>
          대시보드
        </NavLink>
      </nav>
    </header>
  );
}
