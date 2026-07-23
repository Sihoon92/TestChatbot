import { useState } from "react";
import { createSession } from "../../api/client";
import { sendMessage } from "../../api/chatRunner";
import { useStore } from "../../store/store";

export default function Composer() {
  const [text, setText] = useState("");
  const streaming = useStore((s) => s.streaming);
  const activeSessionId = useStore((s) => s.activeSessionId);
  const setActiveSession = useStore((s) => s.setActiveSession);
  const setMessages = useStore((s) => s.setMessages);

  const onSend = async () => {
    const content = text.trim();
    if (!content || streaming) return;

    // 활성 세션이 없으면 자동으로 새 세션을 만든다 (바로 입력 → 전송이 동작하도록).
    // 세션 목록은 스트리밍 완료(onDone) 시 서버에서 새로고침되므로 여기서 건드리지 않는다.
    let sessionId = activeSessionId;
    if (!sessionId) {
      const session = await createSession();
      sessionId = session.id;
      setActiveSession(session.id);
      setMessages([]);
    }

    setText("");
    await sendMessage(sessionId, content);
  };

  return (
    <div className="flex gap-2 border-t border-paper-dark p-3">
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            onSend();
          }
        }}
        rows={2}
        placeholder="메시지를 입력하세요…"
        className="flex-1 resize-none rounded-md border border-paper-dark bg-white p-2 text-sm outline-none"
      />
      <button
        onClick={onSend}
        disabled={streaming}
        className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-dark disabled:opacity-50"
      >
        전송
      </button>
    </div>
  );
}
