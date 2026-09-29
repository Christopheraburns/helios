import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";
import { Children, isValidElement, type ReactNode } from "react";

import MermaidBlock from "./MermaidBlock";

interface MarkdownViewProps {
  markdown: string;
}

function slugifyHeading(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-");
}

export function headingSlugFromText(text: string): string {
  return slugifyHeading(text);
}

function mermaidChartFromPre(children: ReactNode): string | null {
  const child = Children.only(children);
  if (!isValidElement<{ className?: string; children?: ReactNode }>(child)) {
    return null;
  }
  const className = child.props.className ?? "";
  if (!className.includes("language-mermaid")) return null;
  return String(child.props.children).replace(/\n$/, "");
}

export default function MarkdownView({ markdown }: MarkdownViewProps) {
  const components: Components = {
    h1: ({ children }) => {
      const text = String(children);
      const id = slugifyHeading(text);
      return (
        <h1 id={id} className="docs-heading docs-heading--1">
          {children}
        </h1>
      );
    },
    h2: ({ children }) => {
      const text = String(children);
      const id = slugifyHeading(text);
      return (
        <h2 id={id} className="docs-heading docs-heading--2">
          {children}
        </h2>
      );
    },
    h3: ({ children }) => {
      const text = String(children);
      const id = slugifyHeading(text);
      return (
        <h3 id={id} className="docs-heading docs-heading--3">
          {children}
        </h3>
      );
    },
    pre: ({ children }) => {
      const chart = mermaidChartFromPre(children);
      if (chart !== null) {
        return <MermaidBlock chart={chart} />;
      }
      return <pre className="docs-pre">{children}</pre>;
    },
    code: ({ className, children, ...props }) => {
      const isBlock = className?.includes("language-");
      if (isBlock) {
        return (
          <code className={className} {...props}>
            {children}
          </code>
        );
      }
      return (
        <code className="docs-inline-code" {...props}>
          {children}
        </code>
      );
    },
    a: ({ href, children, ...props }) => (
      <a
        className="docs-link"
        href={href}
        target={href?.startsWith("http") ? "_blank" : undefined}
        rel={href?.startsWith("http") ? "noreferrer" : undefined}
        {...props}
      >
        {children}
      </a>
    ),
    table: ({ children }) => (
      <div className="docs-table-wrap">
        <table className="docs-table">{children}</table>
      </div>
    ),
  };

  return (
    <article className="docs-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {markdown}
      </ReactMarkdown>
    </article>
  );
}
