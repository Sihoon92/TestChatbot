import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 백엔드 포트는 기본 8000, 환경변수 BACKEND_PORT 로 덮어쓸 수 있다(dev.py 와 동일 규칙).
const backendPort = process.env.BACKEND_PORT ?? "8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // 0.0.0.0 바인딩: localhost 뿐 아니라 PC의 LAN IP로도 접속 가능하게 한다.
    // (백엔드는 그대로 127.0.0.1 이어도 된다 — /api 프록시는 이 서버 프로세스
    // 안에서 localhost:backendPort 로 연결되므로 접속 주소와 무관하다.)
    host: true,
    // 개발 중 /api 요청을 FastAPI 백엔드로 프록시한다(동일 출처 → CORS 회피).
    proxy: { "/api": `http://localhost:${backendPort}` },
  },
});
