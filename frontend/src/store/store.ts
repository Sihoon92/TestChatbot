import { create } from "zustand";
import type { ChatMessage, LlmHealth, Session } from "../types";

interface State {
  sessions: Session[];
  activeSessionId: string | null;
  messages: ChatMessage[];
  llm: LlmHealth | null;
  streaming: boolean;

  setSessions: (s: Session[]) => void;
  setActiveSession: (id: string | null) => void;
  setMessages: (m: ChatMessage[]) => void;
  appendUserMessage: (content: string) => void;
  startAssistantMessage: () => void;
  appendAssistantDelta: (delta: string) => void;
  setLastAssistantContent: (content: string) => void;
  setLlm: (health: LlmHealth) => void;
  setStreaming: (v: boolean) => void;
}

export const useStore = create<State>((set) => ({
  sessions: [],
  activeSessionId: null,
  messages: [],
  llm: null,
  streaming: false,

  setSessions: (sessions) => set({ sessions }),
  setActiveSession: (activeSessionId) => set({ activeSessionId }),
  setMessages: (messages) => set({ messages }),
  appendUserMessage: (content) =>
    set((s) => ({ messages: [...s.messages, { role: "user", content }] })),
  startAssistantMessage: () =>
    set((s) => ({ messages: [...s.messages, { role: "assistant", content: "" }] })),
  appendAssistantDelta: (delta) =>
    set((s) => {
      const messages = s.messages.slice();
      const last = messages[messages.length - 1];
      if (last && last.role === "assistant") {
        messages[messages.length - 1] = { ...last, content: last.content + delta };
      }
      return { messages };
    }),
  setLastAssistantContent: (content) =>
    set((s) => {
      const messages = s.messages.slice();
      const last = messages[messages.length - 1];
      if (last && last.role === "assistant") {
        messages[messages.length - 1] = { ...last, content };
      }
      return { messages };
    }),
  setLlm: (llm) => set({ llm }),
  setStreaming: (streaming) => set({ streaming }),
}));
