import { Handle, NodeProps, Position } from "@xyflow/react";

import { HeliosFlowNode } from "./reactFlowAdapter";

export default function HeliosNode({ data, selected }: NodeProps<HeliosFlowNode>) {
  const node = data.graphNode;

  return (
    <article
      className={`canvas-node canvas-node--${node.category}${
        selected ? " canvas-node--selected" : ""
      } canvas-node--state-${stateClass(node.status)}`}
    >
      <Handle type="target" position={Position.Left} />
      <div className="canvas-node__kind">{node.kind}</div>
      <strong>{node.label}</strong>
      <span>{formatState(node.status)}</span>
      {node.confidence !== null ? (
        <span>{Math.round(node.confidence * 100)}% confidence</span>
      ) : null}
      <Handle type="source" position={Position.Right} />
    </article>
  );
}

function stateClass(status: string): string {
  return status.toLowerCase().replaceAll("_", "-");
}

function formatState(status: string): string {
  return status
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
