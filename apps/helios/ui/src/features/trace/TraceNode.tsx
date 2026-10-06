import { Handle, NodeProps, Position } from "@xyflow/react";

export interface TraceNodeData extends Record<string, unknown> {
  label: string;
  detail: string;
  category:
    | "question"
    | "llm"
    | "tool"
    | "server"
    | "semantic"
    | "dataset"
    | "document"
    | "query"
    | "result";
  status: string;
  latencyMs?: number | null;
  tokens?: number;
  explanation?: string;
  semanticId?: string;
}

/** A heading over one lane of the answer path; not a step. */
export function LaneLabelNode({ data }: NodeProps) {
  const lane = data as { label: string; lane: string };
  return <div className={`trace-lane trace-lane--${lane.lane}`}>{lane.label}</div>;
}

export default function TraceNode({ data, selected }: NodeProps) {
  const node = data as TraceNodeData;
  return (
    <div
      className={`trace-node trace-node--${node.category}${
        selected ? " trace-node--selected" : ""
      }`}
    >
      <Handle type="target" position={Position.Left} />
      <p className="trace-node__category">{node.category}</p>
      <strong>{node.label}</strong>
      <span>{node.detail}</span>
      <div className="trace-node__metrics">
        <span>{node.status}</span>
        {node.latencyMs != null ? (
          <span>{Math.round(node.latencyMs)} ms</span>
        ) : null}
        {node.tokens ? <span>{node.tokens} tokens</span> : null}
      </div>
      {node.explanation ? (
        <details className="trace-node__help">
          <summary aria-label={`Explain ${node.label}`}>What is this?</summary>
          <p>{node.explanation}</p>
        </details>
      ) : null}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
