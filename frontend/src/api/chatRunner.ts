import type { StreamHandlers } from "./sse";
import { streamChat } from "./sse";
import { listSessions } from "./client";
import { useStore } from "../store/store";

// 스트리밍 토큰을 버퍼에 모았다가 done 시점에 한 번에 렌더한다. 마크다운(표/코드/
// mermaid)은 조각 단위로 파싱하면 깨지므로, 완성된 응답을 통째로 렌더해야 표·볼드·
// 다이어그램이 올바르게 그려진다.
function makeHandlers(): StreamHandlers {
  let buffer = "";
  return {
    onToken: (d) => {
      buffer += d;
    },
    onDone: () => {
      const st = useStore.getState();
      if (buffer) st.setLastAssistantContent(buffer);
      st.setStreaming(false);
      // 제목 자동유도/정렬 갱신을 반영하려고 세션 목록을 새로고침한다.
      listSessions().then(st.setSessions).catch(() => {});
    },
    onError: (msg) => {
      const st = useStore.getState();
      st.setLastAssistantContent(buffer ? `${buffer}\n\n[오류] ${msg}` : `[오류] ${msg}`);
      st.setStreaming(false);
    },
  };
}

function beginTurn(content: string) {
  const st = useStore.getState();
  st.appendUserMessage(content);
  st.startAssistantMessage();
  st.setStreaming(true);
}

export async function sendMessage(sessionId: string, content: string): Promise<void> {
  beginTurn(content);
  await streamChat(sessionId, content, makeHandlers());
}
