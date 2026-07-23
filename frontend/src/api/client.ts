import type { ChatMessage, LlmHealth, Session } from "../types";
import { API_HEADERS } from "./headers";

const BASE = "/api";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

export function listSessions(): Promise<Session[]> {
  return fetch(`${BASE}/sessions`, { method: "GET", headers: API_HEADERS }).then(json<Session[]>);
}

export function createSession(title?: string): Promise<Session> {
  return fetch(`${BASE}/sessions`, {
    method: "POST",
    headers: { ...API_HEADERS, "Content-Type": "application/json" },
    body: JSON.stringify({ title: title ?? null }),
  }).then(json<Session>);
}

export function renameSession(id: string, title: string): Promise<Session> {
  return fetch(`${BASE}/sessions/${id}`, {
    method: "PATCH",
    headers: { ...API_HEADERS, "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  }).then(json<Session>);
}

export async function deleteSession(id: string): Promise<void> {
  const res = await fetch(`${BASE}/sessions/${id}`, { method: "DELETE", headers: API_HEADERS });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

export function getMessages(id: string): Promise<{ messages: ChatMessage[] }> {
  return fetch(`${BASE}/sessions/${id}/messages`, { method: "GET", headers: API_HEADERS }).then(
    json<{ messages: ChatMessage[] }>,
  );
}

export function checkLlm(): Promise<LlmHealth> {
  return fetch(`${BASE}/health/llm`, { method: "GET", headers: API_HEADERS }).then(
    json<LlmHealth>,
  );
}
