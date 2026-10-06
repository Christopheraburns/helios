/** A form over the crawler settings document, section by section, in place of
 * editing its JSON. It edits a plain object and reports the whole new document
 * on every change; validation, versions and saving stay with the caller. A
 * "Try" button on a rule asks the caller to show what it matches. */
import { type ReactNode, useId, useState } from "react";

export type Settings = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

/** One rule of a settings document that can be tried on sample text. */
export interface TryRule {
  type: "pattern" | "label" | "contextual" | "class_cue" | "claim";
  name?: string;
  label?: string;
  class?: string;
  predicate?: string;
}

export interface Vocabulary {
  classes: string[];
  predicates: string[];
}

interface Props {
  settings: Settings;
  vocabulary: Vocabulary | null;
  onChange: (next: Settings) => void;
  onTry: (rule: TryRule, title: string) => void;
}

const TABS = [
  { id: "documents", label: "Documents" },
  { id: "identifiers", label: "Identifiers" },
  { id: "names", label: "Names" },
  { id: "cases", label: "Cases" },
  { id: "claims", label: "Claims" },
  { id: "llm", label: "LLM crawler" },
] as const;
type TabId = (typeof TABS)[number]["id"];

const PATTERN_KINDS: Record<string, string> = {
  key: "A key looked up in the warehouse",
  partial_key: "The end of such a key",
  document_id: "An ID that exists only in documents",
  value: "A value (money, a date)",
};
const LABEL_KINDS: Record<string, string> = {
  key: "A key looked up in the warehouse",
  display: "A name",
  document_id: "An ID that exists only in documents",
  value: "A value",
};

/** A copy of ``root`` with the value at ``path`` replaced. */
export function setAt(root: Settings, path: (string | number)[], value: unknown): Settings {
  if (path.length === 0) return value as Settings;
  const [head, ...rest] = path;
  const copy: any = Array.isArray(root) ? [...root] : { ...(root ?? {}) }; // eslint-disable-line @typescript-eslint/no-explicit-any
  copy[head] = setAt(copy[head], rest, value);
  return copy;
}

function without<T>(items: T[], index: number): T[] {
  return items.filter((_item, at) => at !== index);
}

/** A list of short strings: type one and press Enter to add it; click × to remove. */
export function Chips({
  label,
  values,
  onChange,
  placeholder,
  mono = false,
  options,
}: {
  label: string;
  values: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  mono?: boolean;
  options?: string[];
}) {
  const [draft, setDraft] = useState("");
  const listId = useId();
  const add = () => {
    const value = draft.trim();
    if (value && !values.includes(value)) onChange([...values, value]);
    setDraft("");
  };
  return (
    <div className={`settings-chips${mono ? " settings-chips--mono" : ""}`}>
      {values.map((value, index) => (
        <span className="settings-chip" key={`${value}-${index}`}>
          {value}
          <button
            type="button"
            aria-label={`Remove ${value} from ${label}`}
            onClick={() => onChange(without(values, index))}
          >
            ×
          </button>
        </span>
      ))}
      <input
        aria-label={`Add to ${label}`}
        value={draft}
        list={options ? listId : undefined}
        placeholder={placeholder ?? "Type and press Enter"}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            add();
          }
        }}
        onBlur={add}
      />
      {options && (
        <datalist id={listId}>
          {options.map((option) => (
            <option key={option} value={option} />
          ))}
        </datalist>
      )}
    </div>
  );
}

function Field({ label, help, children }: { label: string; help?: string; children: ReactNode }) {
  return (
    <label className="settings-field">
      <span className="settings-field__label">{label}</span>
      {children}
      {help && <span className="settings-field__help">{help}</span>}
    </label>
  );
}

function NumberInput({
  label,
  help,
  value,
  onChange,
  step,
}: {
  label: string;
  help?: string;
  value: number | null | undefined;
  onChange: (next: number | null) => void;
  step?: number;
}) {
  return (
    <Field label={label} help={help}>
      <input
        type="number"
        step={step}
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value === "" ? null : Number(event.target.value))}
      />
    </Field>
  );
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (next: boolean) => void }) {
  return (
    <label className="settings-toggle">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /> {label}
    </label>
  );
}

function ClassInput({
  label,
  value,
  classes,
  onChange,
}: {
  label: string;
  value: string | null | undefined;
  classes: string[];
  onChange: (next: string | null) => void;
}) {
  const listId = useId();
  return (
    <>
      <input
        aria-label={label}
        list={listId}
        value={value ?? ""}
        placeholder="Class"
        onChange={(event) => onChange(event.target.value.trim() || null)}
      />
      <datalist id={listId}>
        {classes.map((name) => (
          <option key={name} value={name} />
        ))}
      </datalist>
    </>
  );
}

function Section({ title, help, children }: { title: string; help?: string; children: ReactNode }) {
  return (
    <section className="settings-section">
      <h3>{title}</h3>
      {help && <p className="muted">{help}</p>}
      {children}
    </section>
  );
}

/** Rules keyed by a name (a class, a claim type), each a list of phrases. */
function KeyedPhrases({
  title,
  help,
  what,
  entries,
  options,
  mono,
  onChange,
  onTry,
}: {
  title: string;
  help: string;
  what: string;
  entries: Record<string, string[]>;
  options: string[];
  mono?: boolean;
  onChange: (next: Record<string, string[]>) => void;
  onTry?: (key: string) => void;
}) {
  const [adding, setAdding] = useState("");
  const listId = useId();
  const add = () => {
    const key = adding.trim();
    if (key && !(key in entries)) onChange({ ...entries, [key]: [] });
    setAdding("");
  };
  return (
    <Section title={title} help={help}>
      {Object.keys(entries).length === 0 && <p className="muted">None yet.</p>}
      {Object.entries(entries).map(([key, phrases]) => (
        <div className="settings-keyed" key={key}>
          <div className="settings-keyed__header">
            <strong>{key}</strong>
            <span className="muted">{phrases.length}</span>
            {onTry && (
              <button type="button" className="crawler-button" onClick={() => onTry(key)}>
                Try
              </button>
            )}
            <button
              type="button"
              className="crawler-button"
              aria-label={`Remove ${what} ${key}`}
              onClick={() => {
                const rest = { ...entries };
                delete rest[key];
                onChange(rest);
              }}
            >
              Remove
            </button>
          </div>
          <Chips
            label={`${title} for ${key}`}
            values={phrases}
            mono={mono}
            onChange={(next) => onChange({ ...entries, [key]: next })}
          />
        </div>
      ))}
      <div className="settings-add">
        <input
          aria-label={`New ${what}`}
          list={listId}
          value={adding}
          placeholder={`Add a ${what}`}
          onChange={(event) => setAdding(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              add();
            }
          }}
        />
        <datalist id={listId}>
          {options
            .filter((option) => !(option in entries))
            .map((option) => (
              <option key={option} value={option} />
            ))}
        </datalist>
        <button type="button" className="crawler-button" onClick={add} disabled={!adding.trim()}>
          Add
        </button>
      </div>
    </Section>
  );
}

export default function SettingsForm({ settings, vocabulary, onChange, onTry }: Props) {
  const [tab, setTab] = useState<TabId>("identifiers");
  const classes = vocabulary?.classes ?? [];
  const set = (path: (string | number)[], value: unknown) => onChange(setAt(settings, path, value));
  const analyzers = settings.analyzers ?? {};
  const patterns: Settings[] = settings.patterns ?? [];
  const labels: Settings[] = settings.pdf_labels ?? [];
  const headerRules: Settings[] = settings.header_rules ?? [];
  const dictionary = settings.dictionary ?? {};
  const cases = settings.cases ?? {};
  const about = settings.about ?? {};
  const resolution = settings.resolution ?? {};
  const claims = settings.claims ?? {};
  const llm = settings.llm ?? {};

  return (
    <div className="settings-form">
      <div className="settings-tabs" role="tablist" aria-label="Settings sections">
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            className={tab === item.id ? "active" : ""}
            onClick={() => setTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === "documents" && (
        <>
          <Section title="Document types" help="Which kinds of document the crawler reads, and how.">
            <div className="settings-grid">
              <Toggle label="PDF" checked={analyzers.pdf?.enabled ?? true} onChange={(v) => set(["analyzers", "pdf", "enabled"], v)} />
              <NumberInput label="PDF pages read" help="Pages beyond this are skipped" value={analyzers.pdf?.max_pages} onChange={(v) => set(["analyzers", "pdf", "max_pages"], v)} />
              <Toggle label="Email" checked={analyzers.email?.enabled ?? true} onChange={(v) => set(["analyzers", "email", "enabled"], v)} />
              <Toggle label="Read the HTML body when an email has no plain text" checked={analyzers.email?.html_when_no_plain ?? true} onChange={(v) => set(["analyzers", "email", "html_when_no_plain"], v)} />
              <Toggle label="Chat threads" checked={analyzers.chat?.enabled ?? true} onChange={(v) => set(["analyzers", "chat", "enabled"], v)} />
              <Toggle label="Plain text" checked={analyzers.text?.enabled ?? true} onChange={(v) => set(["analyzers", "text", "enabled"], v)} />
              <NumberInput label="Longest text file (characters)" value={analyzers.text?.max_chars} onChange={(v) => set(["analyzers", "text", "max_chars"], v)} />
              <Toggle label="Table rows" checked={analyzers.row?.enabled ?? true} onChange={(v) => set(["analyzers", "row", "enabled"], v)} />
            </div>
            <Field label="Email headers that identify people" help="Usually From; add To or Cc if those people matter">
              <Chips label="email identity headers" values={analyzers.email?.identity_headers ?? []} onChange={(v) => set(["analyzers", "email", "identity_headers"], v)} />
            </Field>
            <Field label="Chat export formats read">
              <Chips label="chat formats" mono values={analyzers.chat?.schemas ?? []} onChange={(v) => set(["analyzers", "chat", "schemas"], v)} />
            </Field>
          </Section>
        </>
      )}

      {tab === "identifiers" && (
        <>
          <Section
            title="Identifier patterns"
            help="Each pattern finds one kind of identifier in text: an account number, an email address, a case number. Use Try to see what it catches."
          >
            {patterns.length === 0 && <p className="muted">No patterns yet.</p>}
            {patterns.map((pattern, index) => (
              <div className="settings-rule" key={index}>
                <div className="settings-rule__row">
                  <Field label="Name">
                    <input
                      aria-label={`Pattern ${index + 1} name`}
                      value={pattern.name ?? ""}
                      onChange={(event) => set(["patterns", index, "name"], event.target.value)}
                    />
                  </Field>
                  <Field label="Kind">
                    <select value={pattern.kind ?? "key"} onChange={(event) => set(["patterns", index, "kind"], event.target.value)}>
                      {Object.entries(PATTERN_KINDS).map(([kind, text]) => (
                        <option key={kind} value={kind}>
                          {text}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Class">
                    <ClassInput label={`Pattern ${index + 1} class`} value={pattern.proposed_class} classes={classes} onChange={(v) => set(["patterns", index, "proposed_class"], v)} />
                  </Field>
                </div>
                <Field label="Pattern (regular expression)">
                  <input
                    className="mono"
                    aria-label={`Pattern ${index + 1} expression`}
                    value={pattern.regex ?? ""}
                    spellCheck={false}
                    onChange={(event) => set(["patterns", index, "regex"], event.target.value)}
                  />
                </Field>
                <div className="settings-rule__row">
                  <NumberInput label="Group" help="0 = the whole match" value={pattern.group ?? 0} onChange={(v) => set(["patterns", index, "group"], v ?? 0)} />
                  <Toggle label="Ignore upper and lower case" checked={pattern.ignore_case ?? false} onChange={(v) => set(["patterns", index, "ignore_case"], v)} />
                  {pattern.kind === "document_id" && (
                    <Field label="Stored as" help="The key name on the entity">
                      <input value={pattern.key_name ?? ""} onChange={(event) => set(["patterns", index, "key_name"], event.target.value.trim() || null)} />
                    </Field>
                  )}
                </div>
                {(pattern.kind === "key" || pattern.kind === "partial_key") && (
                  <Field label="Warehouse columns to look the value up in">
                    <Chips label={`pattern ${index + 1} columns`} mono values={pattern.columns ?? []} onChange={(v) => set(["patterns", index, "columns"], v)} />
                  </Field>
                )}
                <div className="settings-rule__actions">
                  <button type="button" className="crawler-button" onClick={() => onTry({ type: "pattern", name: pattern.name }, `Pattern “${pattern.name}”`)}>
                    Try
                  </button>
                  <button type="button" className="crawler-button" aria-label={`Remove pattern ${pattern.name || index + 1}`} onClick={() => set(["patterns"], without(patterns, index))}>
                    Remove
                  </button>
                </div>
              </div>
            ))}
            <button
              type="button"
              className="crawler-button"
              onClick={() =>
                set(["patterns"], [...patterns, { name: `pattern_${patterns.length + 1}`, regex: "", group: 0, kind: "document_id", proposed_class: null, columns: [], ignore_case: false, key_name: null }])
              }
            >
              Add a pattern
            </button>
          </Section>

          <Section title="PDF field labels" help="Labels printed on forms and reports, and what kind of value follows each.">
            {labels.length === 0 && <p className="muted">No labels yet.</p>}
            {labels.map((label, index) => (
              <div className="settings-rule settings-rule--compact" key={index}>
                <div className="settings-rule__row">
                  <Field label="Label">
                    <input aria-label={`Label ${index + 1}`} value={label.label ?? ""} onChange={(event) => set(["pdf_labels", index, "label"], event.target.value)} />
                  </Field>
                  <Field label="Its value is">
                    <select value={label.kind ?? "key"} onChange={(event) => set(["pdf_labels", index, "kind"], event.target.value)}>
                      {Object.entries(LABEL_KINDS).map(([kind, text]) => (
                        <option key={kind} value={kind}>
                          {text}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Class">
                    <ClassInput label={`Label ${index + 1} class`} value={label.proposed_class} classes={classes} onChange={(v) => set(["pdf_labels", index, "proposed_class"], v)} />
                  </Field>
                </div>
                {label.kind === "key" && (
                  <Field label="Warehouse columns">
                    <Chips label={`label ${index + 1} columns`} mono values={label.columns ?? []} onChange={(v) => set(["pdf_labels", index, "columns"], v)} />
                  </Field>
                )}
                <div className="settings-rule__actions">
                  <button type="button" className="crawler-button" onClick={() => onTry({ type: "label", label: label.label }, `Label “${label.label}”`)}>
                    Try
                  </button>
                  <button type="button" className="crawler-button" aria-label={`Remove label ${label.label || index + 1}`} onClick={() => set(["pdf_labels"], without(labels, index))}>
                    Remove
                  </button>
                </div>
              </div>
            ))}
            <button type="button" className="crawler-button" onClick={() => set(["pdf_labels"], [...labels, { label: "", proposed_class: null, columns: [], kind: "value" }])}>
              Add a label
            </button>
          </Section>

          <Section title="Who an email header names" help="For example: the display name on the From line is a person of this class.">
            {headerRules.map((rule, index) => (
              <div className="settings-rule__row" key={index}>
                <Field label="Header field">
                  <input aria-label={`Header rule ${index + 1} field`} value={rule.field ?? ""} onChange={(event) => set(["header_rules", index, "field"], event.target.value)} />
                </Field>
                <Field label="Class">
                  <ClassInput label={`Header rule ${index + 1} class`} value={rule.proposed_class} classes={classes} onChange={(v) => set(["header_rules", index, "proposed_class"], v ?? "")} />
                </Field>
                <button type="button" className="crawler-button" aria-label={`Remove header rule ${index + 1}`} onClick={() => set(["header_rules"], without(headerRules, index))}>
                  Remove
                </button>
              </div>
            ))}
            <button type="button" className="crawler-button" onClick={() => set(["header_rules"], [...headerRules, { field: "display_name", proposed_class: "" }])}>
              Add a header rule
            </button>
          </Section>

          <Section title="Date formats" help="How dates are written in the documents, in strptime notation (%B %d, %Y is “June 14, 2001”). ISO dates are always read.">
            <Chips label="date formats" mono values={settings.date_formats ?? []} onChange={(v) => set(["date_formats"], v)} />
          </Section>
        </>
      )}

      {tab === "names" && (
        <>
          <KeyedPhrases
            title="Words that confirm a name"
            what="class"
            help="A name that is also an ordinary word counts only near one of these, for its class: “store” near a store’s name. Each entry is a regular expression, matched without regard to case."
            entries={settings.class_cues ?? {}}
            options={classes}
            mono
            onChange={(next) => set(["class_cues"], next)}
            onTry={(key) => onTry({ type: "class_cue", class: key }, `Words that confirm a ${key}`)}
          />
          <Section title="How near">
            <NumberInput label="Characters either side of a name" value={settings.cue_window} onChange={(v) => set(["cue_window"], v)} />
          </Section>
          <KeyedPhrases
            title="Phrases that refer to something already named"
            what="class"
            help="“the item”, “this account”: linked to the one thing of that class the document or case names."
            entries={settings.contextual ?? {}}
            options={classes}
            onChange={(next) => set(["contextual"], next)}
            onTry={(key) => onTry({ type: "contextual", class: key }, `Phrases that refer to a ${key}`)}
          />
          <Section title="Ordinary words" help="Words that are names in the warehouse but also everyday text. A name made only of these needs confirming.">
            <Chips label="ordinary words" values={dictionary.ordinary_words ?? []} onChange={(v) => set(["dictionary", "ordinary_words"], v)} />
            <div className="settings-grid">
              <NumberInput label="Shortest name that stands alone (letters)" value={dictionary.short_token} onChange={(v) => set(["dictionary", "short_token"], v)} />
              <NumberInput label="Most rows a name may be shared by" value={dictionary.max_shared_instances} onChange={(v) => set(["dictionary", "max_shared_instances"], v)} />
              <NumberInput label="Longest name (words)" value={dictionary.max_form_tokens} onChange={(v) => set(["dictionary", "max_form_tokens"], v)} />
            </div>
            <p className="muted">These are judged against the warehouse dictionary, so they cannot be tried here; run a crawl to see their effect.</p>
          </Section>
        </>
      )}

      {tab === "cases" && (
        <>
          <Section title="What links documents into one case" help="Documents sharing one of these identifiers belong together.">
            <div className="settings-checks">
              {patterns.filter((p) => p.name).map((pattern) => {
                const chosen: string[] = cases.identifiers ?? [];
                const on = chosen.includes(pattern.name);
                return (
                  <label key={pattern.name} className="settings-toggle">
                    <input
                      type="checkbox"
                      checked={on}
                      onChange={() => set(["cases", "identifiers"], on ? chosen.filter((n) => n !== pattern.name) : [...chosen, pattern.name])}
                    />{" "}
                    {pattern.name}
                  </label>
                );
              })}
              {patterns.length === 0 && <span className="muted">Add identifier patterns first.</span>}
            </div>
            <div className="settings-grid">
              <NumberInput label="Days apart two documents of a case may be" value={cases.date_window_days} onChange={(v) => set(["cases", "date_window_days"], v)} />
              <NumberInput label="Most documents a weak identifier may link" value={cases.max_hub_documents} onChange={(v) => set(["cases", "max_hub_documents"], v)} />
            </div>
          </Section>
          <Section title="What a document is about" help="When several things are named and no case decides.">
            <Field label="Classes, most likely subject first">
              <Chips label="class priority" values={about.class_priority ?? []} options={classes} onChange={(v) => set(["about", "class_priority"], v)} />
            </Field>
            <Field label="Parts of a document that are its title">
              <Chips label="title segments" mono values={about.title_segments ?? []} onChange={(v) => set(["about", "title_segments"], v)} />
            </Field>
          </Section>
          <Section title="Resolving a name on its own" help="Tuning for names the case could not settle.">
            <div className="settings-grid">
              <NumberInput label="Most rows a name may match" value={resolution.max_candidates} onChange={(v) => set(["resolution", "max_candidates"], v)} />
              <NumberInput label="How close a runner-up blocks a definite link" step={0.01} value={resolution.close} onChange={(v) => set(["resolution", "close"], v)} />
              <NumberInput label="Score factor for an ordinary-word name" step={0.05} value={resolution.low_specificity_factor} onChange={(v) => set(["resolution", "low_specificity_factor"], v)} />
              <NumberInput label="Possible links kept" value={resolution.top_possible} onChange={(v) => set(["resolution", "top_possible"], v)} />
            </div>
          </Section>
        </>
      )}

      {tab === "claims" && (
        <>
          <KeyedPhrases
            title="Cue phrases per claim"
            what="claim type"
            help="A sentence containing one of these phrases states that claim. * stands for up to four words: “refund * approved”."
            entries={claims.cues ?? {}}
            options={vocabulary?.predicates ?? []}
            onChange={(next) => set(["claims", "cues"], next)}
            onTry={(key) => onTry({ type: "claim", predicate: key }, `Claim ${key.replace(/_/g, " ").toLowerCase()}`)}
          />
          <Section title="Words that cancel a cue">
            <Field label="Negations" help="Just before a cue: “not damaged”">
              <Chips label="negations" values={claims.negations ?? []} onChange={(v) => set(["claims", "negations"], v)} />
            </Field>
            <Field label="Hedges" help="Earlier in the same clause: “if it was approved”">
              <Chips label="hedges" values={claims.hedges ?? []} onChange={(v) => set(["claims", "hedges"], v)} />
            </Field>
          </Section>
        </>
      )}

      {tab === "llm" && (
        <Section title="LLM crawler" help="Used only by crawls started with the LLM strategy. The model is the project’s default.">
          <Field label="Instructions" help="The classes, claim types and reply format are added automatically.">
            <textarea className="settings-instructions" value={llm.instructions ?? ""} onChange={(event) => set(["llm", "instructions"], event.target.value)} />
          </Field>
          <div className="settings-grid">
            <Field label="Prompt version" help="Change it when you change the instructions">
              <input value={llm.prompt_version ?? ""} onChange={(event) => set(["llm", "prompt_version"], event.target.value)} />
            </Field>
            <NumberInput label="Longest document sent (characters)" value={llm.max_asset_chars} onChange={(v) => set(["llm", "max_asset_chars"], v)} />
            <NumberInput label="Documents sent at once" value={llm.concurrency} onChange={(v) => set(["llm", "concurrency"], v)} />
            <NumberInput label="Price per million input tokens (USD)" step={0.01} value={llm.input_price_per_million} onChange={(v) => set(["llm", "input_price_per_million"], v)} />
            <NumberInput label="Price per million output tokens (USD)" step={0.01} value={llm.output_price_per_million} onChange={(v) => set(["llm", "output_price_per_million"], v)} />
          </div>
        </Section>
      )}
    </div>
  );
}
