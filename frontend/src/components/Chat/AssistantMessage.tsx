import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import MermaidDiagram from "./MermaidDiagram";

const components: Components = {
  // 코드블록 렌더러: mermaid 는 다이어그램으로, 그 외 펜스 블록은 <pre>, 인라인은 <code>.
  code({ className, children, node, ...props }) {
    const text = String(children).replace(/\n$/, "");
    if (className?.includes("language-mermaid")) {
      return <MermaidDiagram code={text} />;
    }
    if (className?.startsWith("language-")) {
      return (
        <pre className="my-2 overflow-x-auto rounded-md bg-paper-dark p-2 text-xs">
          <code {...props}>{text}</code>
        </pre>
      );
    }
    return (
      <code className="rounded bg-paper-dark px-1 py-0.5 text-[0.85em]" {...props}>
        {children}
      </code>
    );
  },
  // 기본 <pre> 래퍼는 제거(코드 렌더러가 직접 <pre>/다이어그램을 그림).
  pre: ({ children }) => <>{children}</>,
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto">
      <table className="border-collapse text-left text-sm">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-paper-dark bg-paper-dark px-2 py-1 font-semibold">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-paper-dark px-2 py-1">{children}</td>
  ),
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noreferrer" className="text-accent underline">
      {children}
    </a>
  ),
};

export default function AssistantMessage({ content }: { content: string }) {
  return (
    <div className="space-y-2 text-sm leading-relaxed [&_li]:ml-4 [&_li]:list-disc [&_ol_li]:list-decimal [&_h2]:font-semibold">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
