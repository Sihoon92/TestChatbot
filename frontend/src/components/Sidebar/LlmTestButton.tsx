import { checkLlm } from "../../api/client";
import { useStore } from "../../store/store";

export default function LlmTestButton() {
  const llm = useStore((s) => s.llm);
  const setLlm = useStore((s) => s.setLlm);

  const dotClass = llm == null ? "bg-gray-400" : llm.ok ? "bg-green-500" : "bg-red-500";

  const onTest = async () => {
    try {
      setLlm(await checkLlm());
    } catch (e) {
      setLlm({ ok: false, models: [], error: String(e) });
    }
  };

  return (
    <button
      onClick={onTest}
      title={llm?.error ?? llm?.models.join(", ") ?? "미확인"}
      className="flex items-center gap-2 w-full rounded-md px-3 py-2 text-sm hover:bg-paper-dark"
    >
      <span data-testid="llm-status" className={`h-2.5 w-2.5 rounded-full ${dotClass}`} />
      LLM 연결 테스트
    </button>
  );
}
