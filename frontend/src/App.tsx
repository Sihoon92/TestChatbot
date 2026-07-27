import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import AppHeader from "./components/AppHeader";
import ChatLayout from "./components/Chat/ChatLayout";
import DashboardPage from "./components/Dashboard/DashboardPage";

// BrowserRouter 를 포함하지 않는 라우트 트리. 테스트가 MemoryRouter 로 감싸
// 임의의 경로에서 렌더할 수 있도록 분리해 export 한다(라우터를 중첩하면
// 안쪽 라우터가 바깥 히스토리를 무시한다).
export function AppRoutes() {
  return (
    <div className="flex h-screen flex-col bg-paper text-ink">
      <AppHeader />
      <Routes>
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route path="/chat" element={<ChatLayout />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/dashboard/:moldNo" element={<DashboardPage />} />
        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Routes>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  );
}
