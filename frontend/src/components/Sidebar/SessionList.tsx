import { useState } from "react";
import type { Session } from "../../types";

interface Props {
  sessions: Session[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}

export default function SessionList({ sessions, activeId, onSelect, onRename, onDelete }: Props) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const startEdit = (s: Session) => {
    setEditingId(s.id);
    setDraft(s.title);
  };

  const commit = () => {
    if (editingId && draft.trim()) onRename(editingId, draft.trim());
    setEditingId(null);
  };

  return (
    <ul className="mt-2 flex flex-col gap-1 overflow-y-auto">
      {sessions.map((s) => (
        <li key={s.id} className="group relative">
          {editingId === s.id ? (
            <input
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={commit}
              onKeyDown={(e) => {
                if (e.key === "Enter") commit();
                if (e.key === "Escape") setEditingId(null);
              }}
              className="w-full rounded-md border border-paper-dark bg-white px-3 py-2 text-sm outline-none"
            />
          ) : (
            <div className="flex items-center">
              <button
                onClick={() => onSelect(s.id)}
                className={`min-w-0 flex-1 truncate rounded-md px-3 py-2 text-left text-sm hover:bg-paper-dark ${
                  s.id === activeId ? "bg-paper-dark font-medium" : ""
                }`}
              >
                {s.title}
              </button>
              <div className="absolute right-1 hidden gap-1 group-hover:flex">
                <button
                  onClick={() => startEdit(s)}
                  title="이름 변경"
                  className="rounded px-1 text-xs hover:bg-paper"
                >
                  ✎
                </button>
                <button
                  onClick={() => onDelete(s.id)}
                  title="삭제"
                  className="rounded px-1 text-xs hover:bg-paper"
                >
                  🗑
                </button>
              </div>
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}
