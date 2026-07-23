import { useEffect, useRef } from "react";
import {
  createSession,
  deleteSession,
  getMessages,
  listSessions,
  renameSession,
} from "../../api/client";
import { useStore } from "../../store/store";
import LlmTestButton from "./LlmTestButton";
import SessionList from "./SessionList";

export default function Sidebar() {
  const sessions = useStore((s) => s.sessions);
  const activeSessionId = useStore((s) => s.activeSessionId);
  const setSessions = useStore((s) => s.setSessions);
  const setActiveSession = useStore((s) => s.setActiveSession);
  const setMessages = useStore((s) => s.setMessages);
  const mountedRef = useRef(false);

  const refresh = async () => {
    const data = await listSessions();
    if (mountedRef.current) setSessions(data);
  };

  useEffect(() => {
    mountedRef.current = true;
    refresh();
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const onNew = async () => {
    const session = await createSession();
    await refresh();
    setActiveSession(session.id);
    setMessages([]);
  };

  const onSelect = async (id: string) => {
    setActiveSession(id);
    setMessages((await getMessages(id)).messages);
  };

  const onRename = async (id: string, title: string) => {
    await renameSession(id, title);
    await refresh();
  };

  const onDelete = async (id: string) => {
    await deleteSession(id);
    if (activeSessionId === id) {
      setActiveSession(null);
      setMessages([]);
    }
    await refresh();
  };

  return (
    <aside className="flex h-full w-64 flex-col border-r border-paper-dark bg-paper p-3">
      <button
        onClick={onNew}
        className="rounded-md bg-accent px-3 py-2 text-sm font-medium text-white hover:bg-accent-dark"
      >
        + 새 세션
      </button>
      <div className="mt-2">
        <LlmTestButton />
      </div>
      <hr className="my-2 border-paper-dark" />
      <SessionList
        sessions={sessions}
        activeId={activeSessionId}
        onSelect={onSelect}
        onRename={onRename}
        onDelete={onDelete}
      />
    </aside>
  );
}
