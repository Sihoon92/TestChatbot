export interface Session {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

export interface LlmHealth {
  ok: boolean;
  models: string[];
  error: string | null;
}
