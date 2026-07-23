import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // 개발 중 /api 요청을 FastAPI 백엔드로 프록시한다(동일 출처 → CORS 회피).
    proxy: { "/api": "http://localhost:8000" },
  },
});
