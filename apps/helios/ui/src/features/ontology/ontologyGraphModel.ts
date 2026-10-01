import type { OntologyGraphPayload } from "../../api/client";

/** Ontology layers, outermost last (core ⊂ pack ⊂ customer extension). */
export const LAYERS = ["core", "pack", "customer"] as const;
export type Layer = (typeof LAYERS)[number];

export interface OntologyClassNode {
  name: string;
  layer: Layer;
  kind: string;
  abstract: boolean;
  description: string;
  parent: string | null;
  depth: number;
  x: number;
  y: number;
}

export interface OntologyLink {
  id: string;
  kind: "is_a" | "range";
  source: string;
  target: string;
  label?: string;
  multivalued?: boolean;
}

export interface OntologyGraphModel {
  classes: OntologyClassNode[];
  links: OntologyLink[];
}

export const COLUMN_WIDTH = 240;
export const ROW_HEIGHT = 46;

function asLayer(value: unknown): Layer {
  return value === "pack" || value === "customer" ? value : "core";
}

/**
 * Turns the ontology graph payload into a drawable model:
 * - classes laid out as a left-to-right `is_a` forest (depth on x, leaves stacked
 *   on y, each parent centred on its children);
 * - `is_a` links, child → parent;
 * - range links, owner class → range class, one per declared attribute (an
 *   attribute inherited by subclasses is drawn once, from the class declaring it).
 */
export function buildOntologyGraphModel(payload: OntologyGraphPayload): OntologyGraphModel {
  const classes = new Map<string, OntologyClassNode>();
  for (const node of payload.nodes) {
    if (node.label !== "Class") continue;
    const p = node.properties ?? {};
    classes.set(node.key, {
      name: node.key,
      layer: asLayer(p.layer),
      kind: typeof p.kind === "string" ? p.kind : "other",
      abstract: Boolean(p.abstract),
      description: typeof p.description === "string" ? p.description : "",
      parent: null,
      depth: 0,
      x: 0,
      y: 0,
    });
  }

  const links: OntologyLink[] = [];
  for (const edge of payload.edges) {
    if (edge.type === "IS_A" && classes.has(edge.from_key) && classes.has(edge.to_key)) {
      classes.get(edge.from_key)!.parent = edge.to_key;
      links.push({
        id: `is_a:${edge.from_key}`,
        kind: "is_a",
        source: edge.from_key,
        target: edge.to_key,
      });
    }
  }

  // Range links: Attribute -RANGE-> Class, drawn from the attribute's declaring class.
  const attributes = new Map(
    payload.nodes.filter((n) => n.label === "Attribute").map((n) => [n.key, n.properties ?? {}]),
  );
  const seen = new Set<string>();
  for (const edge of payload.edges) {
    if (edge.type !== "RANGE" || !classes.has(edge.to_key)) continue;
    const props = attributes.get(edge.from_key) ?? {};
    const [owningClass, slotFromKey] = edge.from_key.split("#");
    const slot = typeof props.slot_name === "string" ? props.slot_name : slotFromKey;
    const owner = typeof props.declared_by === "string" ? props.declared_by : owningClass;
    if (!classes.has(owner)) continue;
    const id = `range:${owner}.${slot}->${edge.to_key}`;
    if (seen.has(id)) continue;
    seen.add(id);
    links.push({
      id,
      kind: "range",
      source: owner,
      target: edge.to_key,
      label: slot,
      multivalued: Boolean(props.multivalued),
    });
  }

  layout(classes);
  return { classes: [...classes.values()], links };
}

const LAYER_ORDER: Record<Layer, number> = { core: 0, pack: 1, customer: 2 };

function layout(classes: Map<string, OntologyClassNode>): void {
  const children = new Map<string, string[]>();
  const roots: string[] = [];
  for (const node of classes.values()) {
    if (node.parent && classes.has(node.parent)) {
      const list = children.get(node.parent) ?? [];
      list.push(node.name);
      children.set(node.parent, list);
    } else {
      roots.push(node.name);
    }
  }
  const order = (a: string, b: string) => {
    const na = classes.get(a)!;
    const nb = classes.get(b)!;
    return LAYER_ORDER[na.layer] - LAYER_ORDER[nb.layer] || a.localeCompare(b);
  };
  // Biggest tree (Thing) first, then the other roots by name.
  const size = (name: string): number =>
    1 + (children.get(name) ?? []).reduce((total, c) => total + size(c), 0);
  roots.sort((a, b) => size(b) - size(a) || a.localeCompare(b));

  let row = 0;
  const place = (name: string, depth: number): number => {
    const node = classes.get(name)!;
    node.depth = depth;
    node.x = depth * COLUMN_WIDTH;
    const kids = (children.get(name) ?? []).sort(order);
    if (kids.length === 0) {
      node.y = row * ROW_HEIGHT;
      row += 1;
    } else {
      const ys = kids.map((child) => place(child, depth + 1));
      node.y = (ys[0] + ys[ys.length - 1]) / 2;
    }
    return node.y;
  };
  for (const root of roots) {
    place(root, 0);
    row += 1; // gap between separate trees
  }
}

/** Names of a class and everything it is connected to, for highlighting. */
export function neighbourhood(model: OntologyGraphModel, name: string, showRanges: boolean): Set<string> {
  const related = new Set([name]);
  for (const link of model.links) {
    if (link.kind === "range" && !showRanges) continue;
    if (link.source === name) related.add(link.target);
    if (link.target === name) related.add(link.source);
  }
  return related;
}

export interface ClassMapping {
  element: string;
  kind: "dataset" | "relationship";
  status: string;
  primary: string[];
  secondary: string[];
  display: string[];
  aliases: string[];
}

/** Ossie elements mapped to a class (MAPS_TO) or materialised as it (MATERIALISES_AS). */
export function mappingsFor(payload: OntologyGraphPayload, className: string): ClassMapping[] {
  const elements = new Map(
    payload.nodes
      .filter((n) => n.label === "OssieElement")
      .map((n) => [n.key, n.properties ?? {}]),
  );
  const list = (value: unknown) => (Array.isArray(value) ? value.map(String) : []);
  return payload.edges
    .filter(
      (e) =>
        e.from_label === "OssieElement" &&
        e.to_label === "Class" &&
        e.to_key === className &&
        (e.type === "MAPS_TO" || e.type === "MATERIALISES_AS"),
    )
    .map((e) => {
      const props = e.properties ?? {};
      return {
        element: e.from_key,
        kind: e.type === "MAPS_TO" ? ("dataset" as const) : ("relationship" as const),
        status: String(elements.get(e.from_key)?.status ?? "ok"),
        primary: list(props.primary),
        secondary: list(props.secondary),
        display: list(props.display),
        aliases: list(props.aliases),
      };
    })
    .sort((a, b) => a.kind.localeCompare(b.kind) || a.element.localeCompare(b.element));
}

/** Class names with at least one mapping whose Ossie element is missing. */
export function brokenClasses(payload: OntologyGraphPayload): Set<string> {
  const missing = new Set(
    payload.nodes
      .filter((n) => n.label === "OssieElement" && n.properties?.status === "not_found")
      .map((n) => n.key),
  );
  return new Set(
    payload.edges
      .filter((e) => e.from_label === "OssieElement" && e.to_label === "Class" && missing.has(e.from_key))
      .map((e) => e.to_key),
  );
}
