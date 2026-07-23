import type { ChatMessage } from "../../types";
import AssistantMessage from "./AssistantMessage";

function LoadingDots() {
  return (
    <div role="status" aria-label="생각 중" className="flex items-center gap-1 py-1">
      <span className="h-2 w-2 animate-bounce rounded-full bg-ink/40 [animation-delay:-0.3s]" />
      <span className="h-2 w-2 animate-bounce rounded-full bg-ink/40 [animation-delay:-0.15s]" />
      <span className="h-2 w-2 animate-bounce rounded-full bg-ink/40" />
    </div>
  );
}

export default function MessageList({
  messages,
  streaming,
}: {
  messages: ChatMessage[];
  streaming: boolean;
}) {
  return (
    <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-4">
      {messages.map((m, i) => {
        const isUser = m.role === "user";
        const isLast = i === messages.length - 1;
        // 생성 중이고 마지막 assistant 메시지가 아직 비어 있으면 로딩 인디케이터 표시.
        const pending = !isUser && streaming && m.content === "" && isLast;
        return (
          <div
            key={i}
            className={`max-w-[80%] rounded-lg px-4 py-2 text-sm ${
              isUser
                ? "self-end whitespace-pre-wrap bg-accent text-white"
                : "self-start bg-white text-ink"
            }`}
          >
            {isUser ? (
              m.content
            ) : pending ? (
              <LoadingDots />
            ) : (
              <AssistantMessage content={m.content} />
            )}
          </div>
        );
      })}
    </div>
  );
}
