import { Handle, NodeProps, Position } from "@xyflow/react";

import { HeliosFlowNode } from "./reactFlowAdapter";

export default function HeliosNode({ data, selected }: NodeProps<HeliosFlowNode>) {
  const node = data.graphNode;

  return (
    <article
      className={`canvas-node canvas-node--${node.category}${
        selected ? " canvas-node--selected" : ""
      }`}
    >
      <Handle type="target" position={Position.Left} />
      <div className="canvas-node__kind">{node.kind}</div>
      <strong>{node.label}</strong>
      <span>{node.status}</span>
      <Handle type="source" position={Position.Right} />
    </article>
  );
}
