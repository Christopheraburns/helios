import { useEffect, useId, useRef, useState } from "react";

interface MermaidBlockProps {
  chart: string;
}

export default function MermaidBlock({ chart }: MermaidBlockProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const reactId = useId().replace(/:/g, "");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const container = containerRef.current;
    if (!container) return;

    void (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          theme: "neutral",
          securityLevel: "strict",
        });
        const id = `mermaid-${reactId}-${Date.now()}`;
        const { svg } = await mermaid.render(id, chart.trim());
        if (cancelled) return;
        container.innerHTML = svg;
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Mermaid render failed.");
        container.innerHTML = "";
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [chart, reactId]);

  if (error) {
    return (
      <pre className="docs-mermaid docs-mermaid--error">{chart}</pre>
    );
  }

  return (
    <div
      className="docs-mermaid"
      ref={containerRef}
      role="img"
      aria-label="Diagram"
    />
  );
}
