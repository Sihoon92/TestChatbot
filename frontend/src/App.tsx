import Sidebar from "./components/Sidebar/Sidebar";
import ChatPanel from "./components/Chat/ChatPanel";

export default function App() {
  return (
    <div className="flex h-screen bg-paper text-ink">
      <Sidebar />
      <main className="flex flex-1 flex-col">
        <ChatPanel />
      </main>
    </div>
  );
}
