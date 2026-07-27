import Sidebar from "../Sidebar/Sidebar";
import ChatPanel from "./ChatPanel";

// 기존 App.tsx 의 본문을 그대로 옮긴 것. 다른 점은 화면 전체 높이(h-screen)를
// 잡지 않는다는 것뿐이다 — 이제 AppHeader 아래의 남은 공간을 채운다.
export default function ChatLayout() {
  return (
    <div className="flex min-h-0 flex-1">
      <Sidebar />
      <main className="flex min-w-0 flex-1 flex-col">
        <ChatPanel />
      </main>
    </div>
  );
}
