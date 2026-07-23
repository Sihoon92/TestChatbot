import { useEffect, useRef, useState } from "react";
import mermaid from "mermaid";

// 한 번만 초기화. securityLevel: "strict" 로 산출 SVG의 스크립트/이벤트를 제거.
mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });

let _idCounter = 0;

export default function MermaidDiagram({ code }: { code: string }) {
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const idRef = useRef(`mermaid-${_idCounter++}`);

  useEffect(() => {
    let cancelled = false;
    setSvg(null);
    setFailed(false);
    mermaid
      .render(idRef.current, code)
      .then(({ svg }) => {
        if (!cancelled) setSvg(svg);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [code]);

  // 파싱 실패 시 원본 코드를 그대로 보여줘 정보 손실을 막는다.
  if (failed) {
    return (
      <pre className="my-2 overflow-x-auto rounded-md bg-paper-dark p-2 text-xs text-ink">
        {code}
      </pre>
    );
  }
  if (svg === null) {
    return <div className="my-2 text-xs text-ink/50">다이어그램 렌더링 중…</div>;
  }
  return (
    <div
      className="my-2 overflow-x-auto"
      // mermaid strict 모드 산출물이라 안전.
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
