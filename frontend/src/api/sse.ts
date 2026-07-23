import { API_HEADERS } from "./headers";

export interface StreamHandlers {
  onToken: (delta: string) => void;
  onDone: () => void;
  onError: (message: string) => void;
}

async function readSSE(res: Response, handlers: StreamHandlers): Promise<void> {
  if (!res.ok || !res.body) {
    handlers.onError(`HTTP ${res.status}`);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE 프레임은 빈 줄(\n\n)로 구분된다. 완성된 프레임만 잘라 파싱한다.
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const event = /event: (.*)/.exec(raw)?.[1]?.trim();
      const dataLine = /data: (.*)/.exec(raw)?.[1] ?? "{}";
      try {
        const data = JSON.parse(dataLine);
        if (event === "token") handlers.onToken(data.delta ?? "");
        else if (event === "done") handlers.onDone();
        else if (event === "error") handlers.onError(data.message ?? "unknown error");
      } catch {
        handlers.onError(`Malformed SSE frame: ${raw}`);
      }
    }
  }
}

export async function streamChat(
  sessionId: string,
  content: string,
  handlers: StreamHandlers,
): Promise<void> {
  const res = await fetch(`/api/sessions/${sessionId}/chat`, {
    method: "POST",
    headers: { ...API_HEADERS, "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  await readSSE(res, handlers);
}
