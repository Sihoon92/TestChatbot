import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 백엔드 포트는 기본 8000, 환경변수 BACKEND_PORT 로 덮어쓸 수 있다(dev.py 와 동일 규칙).
const backendPort = process.env.BACKEND_PORT ?? "8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // 개발 중 /api 요청을 FastAPI 백엔드로 프록시한다(동일 출처 → CORS 회피).
    proxy: { "/api": `http://localhost:${backendPort}` },
  },
});
