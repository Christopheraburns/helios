import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMemo } from "react";
import {
  neighbourhood,
  type Layer,
  type OntologyClassNode,
  type OntologyGraphModel,
} from "./ontologyGraphModel";

export const LAYER_COLOURS: Record<Layer, { fill: string; border: string; label: string }> = {
  core: { fill: "#eef2f7", border: "#64748b", label: "Core" },
  pack: { fill: "#e0ecff", border: "#2563eb", label: "Domain pack" },
  customer: { fill: "#fff4dd", border: "#d97706", label: "Customer extension" },
};

// React Flow node data must be a plain record.
type ClassNodeData = Record<string, unknown> &
  OntologyClassNode & { dimmed: boolean; selected: boolean; matched: boolean; broken: boolean };

function ClassNode({ data }: NodeProps<Node<ClassNodeData>>) {
  const colours = LAYER_COLOURS[data.layer];
  return (
    <div
      className={[
        "ontology-node",
        data.abstract ? "ontology-node--abstract" : "",
        data.selected ? "ontology-node--selected" : "",
        data.matched ? "ontology-node--matched" : "",
        data.dimmed ? "ontology-node--dimmed" : "",
        data.broken ? "ontology-node--broken" : "",
      ].join(" ")}
      style={{ background: colours.fill, borderColor: colours.border }}
      title={data.broken ? "A mapped Ossie element is missing from the model" : data.description || data.name}
    >
      <Handle type="target" position={Position.Left} />
      <span className="ontology-node__name">{data.name}</span>
      <span className="ontology-node__kind">{data.abstract ? `${data.kind} · abstract` : data.kind}</span>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const nodeTypes = { ontologyClass: ClassNode };

interface OntologyGraphViewProps {
  model: OntologyGraphModel;
  broken: Set<string>;
  layers: Set<Layer>;
  showRanges: boolean;
  selected: string | null;
  search: string;
  onSelect: (name: string | null) => void;
}

/** The ontology as a graph: the is_a forest left to right, attribute ranges on top. */
export default function OntologyGraphView({
  model,
  broken,
  layers,
  showRanges,
  selected,
  search,
  onSelect,
}: OntologyGraphViewProps) {
  const focus = useMemo(
    () => (selected ? neighbourhood(model, selected, showRanges) : null),
    [model, selected, showRanges],
  );
  const query = search.trim().toLowerCase();

  const nodes = useMemo<Node<ClassNodeData>[]>(
    () =>
      model.classes.map((c) => ({
        id: c.name,
        type: "ontologyClass",
        position: { x: c.x, y: c.y },
        data: {
          ...c,
          selected: c.name === selected,
          broken: broken.has(c.name),
          matched: query.length > 0 && c.name.toLowerCase().includes(query),
          // Dimmed: outside the chosen layers, or outside the selection's neighbourhood.
          dimmed: !layers.has(c.layer) || (focus !== null && !focus.has(c.name)),
        },
      })),
    [model, broken, layers, selected, focus, query],
  );

  const edges = useMemo<Edge[]>(
    () =>
      model.links
        .filter((l) => l.kind === "is_a" || showRanges)
        // With a class selected, only its own edges are drawn.
        .filter((l) => selected === null || l.source === selected || l.target === selected)
        .map((l) => {
          const touchesSelection = selected !== null;
          const faded = false;
          if (l.kind === "is_a") {
            // Drawn parent → child (left to right); the arrowhead sits at the
            // parent, as in UML generalisation.
            return {
              id: l.id,
              source: l.target,
              target: l.source,
              type: "smoothstep",
              markerStart: { type: MarkerType.ArrowClosed, color: "#94a3b8" },
              style: { stroke: "#94a3b8", opacity: faded ? 0.25 : 1 },
            };
          }
          return {
            id: l.id,
            source: l.source,
            target: l.target,
            type: "default",
            // Attribute names only for the selected class's edges, so labels never
            // float over unrelated parts of the graph.
            label: touchesSelection ? (l.multivalued ? `${l.label} *` : l.label) : undefined,
            labelStyle: { fontSize: 11, fill: "#7c2d12" },
            labelBgStyle: { fill: "#fff7ed" },
            markerEnd: { type: MarkerType.ArrowClosed, color: "#ea580c" },
            style: {
              stroke: "#ea580c",
              strokeDasharray: "6 4",
              opacity: faded ? 0.15 : touchesSelection ? 1 : 0.7,
            },
            animated: touchesSelection,
          };
        }),
    [model, showRanges, selected],
  );

  return (
    <div className="ontology-graph" data-testid="ontology-graph">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={(_, node) => onSelect(node.id === selected ? null : node.id)}
        onPaneClick={() => onSelect(null)}
        nodesDraggable={false}
        nodesConnectable={false}
        fitView
        minZoom={0.1}
        proOptions={{ hideAttribution: true }}
      >
        <Background />
        <Controls showInteractive={false} />
        <MiniMap
          pannable
          zoomable
          nodeColor={(n) => LAYER_COLOURS[(n.data as unknown as ClassNodeData).layer].border}
        />
      </ReactFlow>
    </div>
  );
}
