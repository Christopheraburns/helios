/** The source mapping as a form: which warehouse table each ontology class is
 * in, which columns identify it, and which records tie a case together.
 * It edits the same document as the JSON view; the parts it does not show
 * (attributes, glossary concepts, resolution scope) are kept as they are. */
import { type ReactNode, useId, useState } from "react";

import type { MappingModel } from "../../api/client";
import { Chips, ClassInput, Field, NumberInput, Section, setAt, Toggle } from "./SettingsForm";

export type Mapping = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
export type ProbeTarget = { entity: string } | { anchor: string };

interface Props {
  mapping: Mapping;
  /** The semantic model the mapping names, when it is published; pickers need it. */
  model: MappingModel | null;
  models: string[];
  /** Class names of the active ontology, or null when none is active. */
  classes: string[] | null;
  onChange: (next: Mapping) => void;
  onProbe: (target: ProbeTarget, title: string) => void;
}

const TABS = [
  { id: "tables", label: "Classes and tables" },
  { id: "anchors", label: "Case records" },
  { id: "other", label: "Relationships and thresholds" },
] as const;
type TabId = (typeof TABS)[number]["id"];

function without<T>(items: T[], index: number): T[] {
  return items.filter((_item, at) => at !== index);
}

/** A text input offering ``options``; anything typed is kept. */
function Pick({
  label,
  value,
  options,
  onChange,
  placeholder,
}: {
  label: string;
  value: string | null | undefined;
  options: string[];
  onChange: (next: string) => void;
  placeholder?: string;
}) {
  const listId = useId();
  return (
    <>
      <input
        aria-label={label}
        className="mono"
        list={listId}
        value={value ?? ""}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value.trim())}
      />
      <datalist id={listId}>
        {options.map((option) => (
          <option key={option} value={option} />
        ))}
      </datalist>
    </>
  );
}

/** "ours=theirs" pairs for a join on several columns. */
function pairsText(pairs: unknown): string[] {
  return Array.isArray(pairs) ? pairs.map((pair) => (Array.isArray(pair) ? `${pair[0]}=${pair[1]}` : String(pair))) : [];
}

function pairsValue(texts: string[]): [string, string][] {
  return texts
    .map((text) => text.split("=").map((part) => part.trim()))
    .filter((parts) => parts.length === 2 && parts[0] && parts[1])
    .map(([ours, theirs]) => [ours, theirs]);
}

export default function MappingForm({ mapping, model, models, classes, onChange, onProbe }: Props) {
  const [tab, setTab] = useState<TabId>("tables");
  const set = (path: (string | number)[], value: unknown) => onChange(setAt(mapping, path, value));
  const entities: Mapping[] = mapping.entities ?? [];
  const anchors: Mapping[] = mapping.anchors ?? [];
  const relationships: Mapping[] = mapping.relationships ?? [];
  const tables = model?.tables ?? [];
  const classNames = classes ?? [];
  const mapped = entities.map((entity) => String(entity.class ?? "")).filter(Boolean);
  const columnsOfTable = (table: string | null | undefined) =>
    tables.find((candidate) => candidate.name === table)?.columns ?? [];
  const tableOfClass = (name: string | null | undefined) =>
    entities.find((entity) => entity.class === name)?.ossie_element as string | undefined;
  const columnsOfClass = (name: string | null | undefined) => columnsOfTable(tableOfClass(name)).map((c) => c.name);

  const joinRows = (path: (string | number)[], joins: Mapping[], ownerClass: string | undefined, what: string) => (
    <>
      {joins.map((join, index) => (
        <div className="settings-rule__row" key={index}>
          <Field label="Column on this record">
            <Pick label={`${what} join ${index + 1} column`} value={join.column} options={columnsOfClass(ownerClass)} onChange={(v) => set([...path, index, "column"], v)} />
          </Field>
          <Field label="Is the key of">
            <Pick label={`${what} join ${index + 1} class`} value={join.class} options={mapped} onChange={(v) => set([...path, index, "class"], v)} />
          </Field>
          <Field label="Relationship">
            <ClassInput label={`${what} join ${index + 1} relationship`} value={join.edge} classes={classNames} onChange={(v) => set([...path, index, "edge"], v ?? "")} />
          </Field>
          <div className="settings-rule__actions">
            <button type="button" className="crawler-button" aria-label={`Remove ${what} join ${index + 1}`} onClick={() => set(path, without(joins, index))}>
              Remove
            </button>
          </div>
        </div>
      ))}
      <button type="button" className="crawler-button" onClick={() => set(path, [...joins, { column: "", class: "", edge: "" }])}>
        Add a joined class
      </button>
    </>
  );

  const dateFields = (path: (string | number)[], date: Mapping | null | undefined, ownerClass: string | undefined, what: string) => (
    <div className="settings-rule__row">
      <Field label="Date column" help="Leave empty if the record has no date">
        <Pick
          label={`${what} date column`}
          value={date?.column}
          options={columnsOfClass(ownerClass)}
          onChange={(v) => set(path, v ? { ...(date ?? {}), column: v } : null)}
        />
      </Field>
      {date?.column ? (
        <>
          <Field label="Date table" help="Only when the column is a key into a table of dates">
            <Pick
              label={`${what} date table`}
              value={date.table}
              options={tables.map((t) => t.name)}
              onChange={(v) => set(path, v ? { ...date, table: v } : { column: date.column })}
            />
          </Field>
          {date.table ? (
            <>
              <Field label="Its key">
                <Pick label={`${what} date table key`} value={date.key} options={columnsOfTable(date.table).map((c) => c.name)} onChange={(v) => set([...path, "key"], v)} />
              </Field>
              <Field label="Its date">
                <Pick label={`${what} date table value`} value={date.value} options={columnsOfTable(date.table).map((c) => c.name)} onChange={(v) => set([...path, "value"], v)} />
              </Field>
            </>
          ) : null}
        </>
      ) : null}
    </div>
  );

  let body: ReactNode = null;
  if (tab === "tables") {
    body = (
      <Section
        title="Classes and their tables"
        help="One entry per ontology class that has records in the warehouse. The crawler looks names and identifiers up in these columns."
      >
        {entities.map((entity, index) => {
          const at = (...rest: (string | number)[]) => ["entities", index, ...rest];
          const ids: Mapping = entity.identifiers ?? {};
          const columns = columnsOfTable(entity.ossie_element);
          const names = columns.map((c) => c.name);
          const suggested = columns.filter((c) => c.role === "identifier").map((c) => c.name);
          const chips = (key: string, label: string, help?: string) => (
            <Field label={label} help={help}>
              <Chips label={`${entity.class || `entry ${index + 1}`} ${label.toLowerCase()}`} mono options={names} values={ids[key] ?? []} onChange={(v) => set(at("identifiers", key), v)} />
            </Field>
          );
          return (
            <div className="settings-rule" key={index}>
              <div className="settings-rule__row">
                <Field label="Class">
                  <ClassInput label={`Entry ${index + 1} class`} value={entity.class} classes={classNames} onChange={(v) => set(at("class"), v ?? "")} />
                </Field>
                <Field label="Table" help={tables.find((t) => t.name === entity.ossie_element)?.label}>
                  <Pick label={`Entry ${index + 1} table`} value={entity.ossie_element} options={tables.map((t) => t.name)} onChange={(v) => set(at("ossie_element"), v)} />
                </Field>
              </div>
              {model && entity.ossie_element && columns.length === 0 && (
                <p className="mapping-warning">Table “{entity.ossie_element}” is not in the semantic model.</p>
              )}
              {chips("primary", "Key", "The column or columns that make a row one record")}
              {suggested.length > 0 && (ids.primary ?? []).length === 0 && (
                <p className="muted">
                  Marked as identifiers when the table was profiled: <span className="mono">{suggested.join(", ")}</span>
                </p>
              )}
              {chips("secondary", "Other identifiers", "IDs people write in documents: an account number, an email address")}
              {chips("display", "Name columns", "What the record is called; several are joined with a space")}
              {chips("aliases", "Other name columns")}
              <Field label="Other ways the name is written" help="Text with column names in braces, for example Dr. {full_name}">
                <Chips label={`${entity.class || `entry ${index + 1}`} name templates`} values={ids.alias_templates ?? []} onChange={(v) => set(at("identifiers", "alias_templates"), v)} />
              </Field>
              <div className="settings-rule__actions">
                <button type="button" className="crawler-button" disabled={!entity.class} onClick={() => onProbe({ entity: entity.class }, `${entity.class} in the warehouse`)}>
                  Check in the warehouse
                </button>
                <button type="button" className="crawler-button" aria-label={`Remove ${entity.class || `entry ${index + 1}`}`} onClick={() => set(["entities"], without(entities, index))}>
                  Remove
                </button>
              </div>
            </div>
          );
        })}
        <button
          type="button"
          className="crawler-button"
          onClick={() => set(["entities"], [...entities, { ossie_element: "", class: "", identifiers: { primary: [], secondary: [], display: [], aliases: [], alias_templates: [] } }])}
        >
          Add a class
        </button>
      </Section>
    );
  } else if (tab === "anchors") {
    body = (
      <Section
        title="Case records"
        help="A case record is the warehouse row a group of documents is about: an order, a visit, a claim. When documents name enough of it, the crawler finds the one row that fits and links everything on it."
      >
        {anchors.map((anchor, index) => {
          const at = (...rest: (string | number)[]) => ["anchors", index, ...rest];
          const lookups: Mapping[] = anchor.lookups ?? [];
          const related: Mapping[] = anchor.related ?? [];
          const colocated: Mapping[] = anchor.colocated ?? [];
          const own = columnsOfClass(anchor.class);
          const identifiable = [anchor.class, ...related.map((r) => r.class)].filter(Boolean);
          const what = anchor.class || `case record ${index + 1}`;
          return (
            <div className="settings-rule" key={index}>
              <div className="settings-rule__row">
                <Field label="Class" help={tableOfClass(anchor.class) ? `Table: ${tableOfClass(anchor.class)}` : "A class from the first tab"}>
                  <Pick label={`Case record ${index + 1} class`} value={anchor.class} options={mapped} onChange={(v) => set(at("class"), v)} />
                </Field>
                <NumberInput label="Most rows that may fit" help="More than this settles nothing" value={anchor.row_limit} onChange={(v) => set(at("row_limit"), v ?? 5)} />
                <NumberInput label="Kinds of fact needed" help="Before the warehouse is asked" value={anchor.min_constraint_kinds} onChange={(v) => set(at("min_constraint_kinds"), v ?? 2)} />
              </div>

              <h4>Identifiers written in documents</h4>
              {lookups.map((lookup, at2) => (
                <div className="settings-rule__row" key={at2}>
                  <Field label="Column">
                    <Pick label={`${what} identifier ${at2 + 1} column`} value={lookup.column} options={own} onChange={(v) => set(at("lookups", at2, "column"), v)} />
                  </Field>
                  <Field label="The written value">
                    <select aria-label={`${what} identifier ${at2 + 1} match`} value={lookup.match ?? "equals"} onChange={(event) => set(at("lookups", at2, "match"), event.target.value)}>
                      <option value="equals">is the whole value</option>
                      <option value="ends_with">is the end of it (last digits)</option>
                    </select>
                  </Field>
                  <Field label="Kind">
                    <select aria-label={`${what} identifier ${at2 + 1} type`} value={lookup.type ?? "integer"} onChange={(event) => set(at("lookups", at2, "type"), event.target.value)}>
                      <option value="integer">a number</option>
                      <option value="string">text</option>
                    </select>
                  </Field>
                  <Field label="Names a" help="When the pattern that found it names no class">
                    <Pick label={`${what} identifier ${at2 + 1} names`} value={lookup.identifies} options={identifiable} onChange={(v) => set(at("lookups", at2, "identifies"), v || null)} />
                  </Field>
                  <Field label="Also found under" help="Column names the crawler's patterns use for it">
                    <Chips label={`${what} identifier ${at2 + 1} other columns`} mono values={lookup.source_columns ?? []} onChange={(v) => set(at("lookups", at2, "source_columns"), v)} />
                  </Field>
                  <div className="settings-rule__actions">
                    <button type="button" className="crawler-button" aria-label={`Remove ${what} identifier ${at2 + 1}`} onClick={() => set(at("lookups"), without(lookups, at2))}>
                      Remove
                    </button>
                  </div>
                </div>
              ))}
              <button type="button" className="crawler-button" onClick={() => set(at("lookups"), [...lookups, { column: "", match: "equals", type: "string", source_columns: [], identifies: null }])}>
                Add an identifier
              </button>

              <h4>Who and what is on the record</h4>
              {joinRows(at("joins"), anchor.joins ?? [], anchor.class, what)}

              <h4>When</h4>
              {dateFields(at("date"), anchor.date, anchor.class, what)}

              <h4>Classes kept on a joined table</h4>
              <p className="muted">For example a brand that is a column of the item’s row, not a table of its own.</p>
              {colocated.map((entry, at2) => (
                <div className="settings-rule__row" key={at2}>
                  <Field label="On the table of">
                    <Pick label={`${what} shared table ${at2 + 1} via`} value={entry.via} options={(anchor.joins ?? []).map((j: Mapping) => j.class)} onChange={(v) => set(at("colocated", at2, "via"), v)} />
                  </Field>
                  <Field label="Class">
                    <Pick label={`${what} shared table ${at2 + 1} class`} value={entry.class} options={mapped} onChange={(v) => set(at("colocated", at2, "class"), v)} />
                  </Field>
                  <div className="settings-rule__actions">
                    <button type="button" className="crawler-button" aria-label={`Remove ${what} shared table ${at2 + 1}`} onClick={() => set(at("colocated"), without(colocated, at2))}>
                      Remove
                    </button>
                  </div>
                </div>
              ))}
              <button type="button" className="crawler-button" onClick={() => set(at("colocated"), [...colocated, { via: "", class: "" }])}>
                Add one
              </button>

              <h4>Records that belong to it</h4>
              <p className="muted">Another row found from this one, such as the sale a return refers to.</p>
              {related.map((record, at2) => {
                const name = record.class || `related record ${at2 + 1}`;
                return (
                  <div className="settings-rule settings-rule--compact" key={at2}>
                    <div className="settings-rule__row">
                      <Field label="Class">
                        <Pick label={`${what} related ${at2 + 1} class`} value={record.class} options={mapped} onChange={(v) => set(at("related", at2, "class"), v)} />
                      </Field>
                      <Field label="Relationship from the case record">
                        <ClassInput label={`${what} related ${at2 + 1} relationship`} value={record.edge} classes={classNames} onChange={(v) => set(at("related", at2, "edge"), v ?? "")} />
                      </Field>
                      <Field label="Joined on" help="this column=its column">
                        <Chips label={`${what} related ${at2 + 1} join columns`} mono values={pairsText(record.join_on)} placeholder="column=column" onChange={(v) => set(at("related", at2, "join_on"), pairsValue(v))} />
                      </Field>
                    </div>
                    {joinRows(at("related", at2, "joins"), record.joins ?? [], record.class, name)}
                    {dateFields(at("related", at2, "date"), record.date, record.class, name)}
                    <Toggle label="The same people and things as the case record (link them once)" checked={!!record.share_anchor_entities} onChange={(v) => set(at("related", at2, "share_anchor_entities"), v)} />
                    <div className="settings-rule__actions">
                      <button type="button" className="crawler-button" aria-label={`Remove ${what} related ${at2 + 1}`} onClick={() => set(at("related"), without(related, at2))}>
                        Remove
                      </button>
                    </div>
                  </div>
                );
              })}
              <button type="button" className="crawler-button" onClick={() => set(at("related"), [...related, { class: "", join_on: [], edge: "", joins: [], date: null, share_anchor_entities: false }])}>
                Add a related record
              </button>

              <div className="settings-rule__actions">
                <button type="button" className="crawler-button" disabled={!anchor.class} onClick={() => onProbe({ anchor: anchor.class }, `The ${anchor.class} query`)}>
                  Run its query
                </button>
                <button type="button" className="crawler-button" aria-label={`Remove case record ${what}`} onClick={() => set(["anchors"], without(anchors, index))}>
                  Remove case record
                </button>
              </div>
            </div>
          );
        })}
        <button
          type="button"
          className="crawler-button"
          onClick={() => set(["anchors"], [...anchors, { class: "", lookups: [], joins: [], colocated: [], date: null, related: [], row_limit: 5, min_constraint_kinds: 2 }])}
        >
          Add a case record
        </button>
      </Section>
    );
  } else {
    const thresholds: Mapping = mapping.resolution?.thresholds ?? {};
    const kept = [
      entities.some((e) => (e.attributes ?? []).length > 0) && "attribute bindings",
      (mapping.concepts ?? []).length > 0 && `${mapping.concepts.length} glossary concepts`,
      (mapping.resolution?.scope ?? []).length > 0 && "resolution scope",
    ].filter(Boolean);
    body = (
      <>
        <Section title="Table relationships" help="Which relationship in the ontology a join between two tables stands for.">
          {relationships.map((relationship, index) => (
            <div className="settings-rule__row" key={index}>
              <Field label="Join in the semantic model">
                <Pick label={`Relationship ${index + 1} join`} value={relationship.ossie_relationship} options={(model?.relationships ?? []).map((r) => r.name)} onChange={(v) => set(["relationships", index, "ossie_relationship"], v)} />
              </Field>
              <Field label="Relationship">
                <ClassInput label={`Relationship ${index + 1} type`} value={relationship.edge} classes={classNames} onChange={(v) => set(["relationships", index, "edge"], v ?? "")} />
              </Field>
              <div className="settings-rule__actions">
                <button type="button" className="crawler-button" aria-label={`Remove relationship ${index + 1}`} onClick={() => set(["relationships"], without(relationships, index))}>
                  Remove
                </button>
              </div>
            </div>
          ))}
          <button type="button" className="crawler-button" onClick={() => set(["relationships"], [...relationships, { ossie_relationship: "", edge: "" }])}>
            Add a relationship
          </button>
        </Section>
        <Section title="How sure a match must be" help="Between 0 and 1. A name that scores lower is recorded as only possibly the same.">
          <div className="settings-grid">
            <NumberInput label="A known other name" step={0.01} value={thresholds.alias_min_score} onChange={(v) => set(["resolution", "thresholds", "alias_min_score"], v ?? 0.92)} />
            <NumberInput label="A near spelling" step={0.01} value={thresholds.fuzzy_min_score} onChange={(v) => set(["resolution", "thresholds", "fuzzy_min_score"], v ?? 0.85)} />
            <NumberInput label="A model's suggestion" step={0.01} value={thresholds.model_assisted_min_confidence} onChange={(v) => set(["resolution", "thresholds", "model_assisted_min_confidence"], v ?? 0.8)} />
          </div>
        </Section>
        {kept.length > 0 && (
          <p className="muted">
            This mapping also has {kept.join(", ")}. The form keeps them unchanged; edit them in the JSON view.
          </p>
        )}
      </>
    );
  }

  return (
    <div className="settings-form">
      <div className="settings-grid">
        <Field label="Semantic model" help={model ? `${model.tables.length} tables` : "Not published: tables and columns cannot be offered or checked"}>
          <Pick label="Semantic model" value={mapping.model} options={models} onChange={(v) => set(["model"], v)} />
        </Field>
        <Field label="Database" help="Where the mapped tables are">
          <input aria-label="Database" className="mono" value={mapping.database ?? ""} placeholder={model?.database} onChange={(event) => set(["database"], event.target.value.trim())} />
        </Field>
        <Field label="Written for ontology version">
          <input aria-label="Ontology version" value={mapping.ontology_version ?? ""} onChange={(event) => set(["ontology_version"], event.target.value.trim())} />
        </Field>
      </div>
      {classes === null && <p className="mapping-warning">No ontology version is active, so class names cannot be offered or checked.</p>}
      <div className="settings-tabs" role="tablist" aria-label="Mapping sections">
        {TABS.map((entry) => (
          <button key={entry.id} type="button" role="tab" aria-selected={tab === entry.id} className={tab === entry.id ? "active" : ""} onClick={() => setTab(entry.id)}>
            {entry.label}
          </button>
        ))}
      </div>
      {body}
    </div>
  );
}
