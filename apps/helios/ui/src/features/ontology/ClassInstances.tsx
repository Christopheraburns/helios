import { Fragment, type ReactNode, useEffect, useState } from "react";

import type {
  CrawlEntity,
  CrawlEntityDetail,
  CrawlEntityDocument,
  HeliosApi,
} from "../../api/client";
import { whereInDocument } from "../../components/EvidenceResults";

type Api = Pick<HeliosApi, "crawlEntities" | "crawlEntity">;

interface Props {
  api: () => Api;
  /** The selected class and its subclasses. */
  classes: string[];
  onSelectClass: (name: string) => void;
}

const DOCUMENT_KINDS: Record<string, string> = {
  "message/rfc822": "Email",
  "application/pdf": "PDF report",
  "application/json": "Chat",
};

const plural = (n: number, one: string, many = `${one}s`) =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`;

function message(err: unknown): string {
  return err instanceof Error ? err.message : "The crawl index could not be read.";
}

/** A passage with each mention of the entity highlighted. */
export function HighlightedText({
  text,
  mentions,
}: {
  text: string;
  mentions: Array<{ start: number | null; end: number | null; resolved_by: string }>;
}) {
  const ranges = mentions
    .filter(
      (m): m is { start: number; end: number; resolved_by: string } =>
        m.start != null && m.end != null && m.start >= 0 && m.end <= text.length && m.start < m.end,
    )
    .sort((a, b) => a.start - b.start);
  const parts: ReactNode[] = [];
  let at = 0;
  ranges.forEach((range, index) => {
    if (range.start < at) return; // overlaps the previous mention
    parts.push(<Fragment key={`t${index}`}>{text.slice(at, range.start)}</Fragment>);
    parts.push(
      <mark key={`m${index}`} title={`Recognised by: ${range.resolved_by}`}>
        {text.slice(range.start, range.end)}
      </mark>,
    );
    at = range.end;
  });
  parts.push(<Fragment key="end">{text.slice(at)}</Fragment>);
  return <p className="instance-passage__text">{parts}</p>;
}

function Document({ document }: { document: CrawlEntityDocument }) {
  const kind = DOCUMENT_KINDS[document.mime_type] ?? document.class;
  return (
    <li className="instance-document">
      <div className="instance-document__header">
        <strong>{kind}</strong>
        {document.timestamp && <span>{document.timestamp.slice(0, 10)}</span>}
        <span className="badge" title="About: the document's subject. Mentions: named in it.">
          {document.relationship === "About" ? "about" : "mentions"}
        </span>
      </div>
      <div className="instance-document__name" title={document.asset_id}>
        {document.name}
      </div>
      {document.passages.length === 0 ? (
        <p className="instance-empty">
          Linked through its return case; not named in the text itself.
        </p>
      ) : (
        document.passages.map((passage) => (
          <div className="instance-passage" key={passage.segment_id}>
            <div className="instance-passage__where">
              {whereInDocument(passage.locator, passage.segment_type)}
            </div>
            <HighlightedText text={passage.text} mentions={passage.mentions} />
          </div>
        ))
      )}
    </li>
  );
}

export default function ClassInstances({ api, classes, onSelectClass }: Props) {
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<{ total: number; entities: CrawlEntity[] } | null>(null);
  const [selected, setSelected] = useState<CrawlEntity | null>(null);
  const [detail, setDetail] = useState<CrawlEntityDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = classes.join(",");

  useEffect(() => {
    setSelected(null);
    setQuery("");
  }, [key]);

  useEffect(() => {
    let active = true;
    setFound(null);
    setError(null);
    const timer = setTimeout(async () => {
      try {
        const client = api();
        if (!client.crawlEntities) throw new Error("The crawler API is unavailable.");
        const loaded = await client.crawlEntities(key.split(","), query, 50);
        if (active) setFound(loaded);
      } catch (err) {
        if (active) setError(message(err));
      }
    }, query ? 300 : 0);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [api, key, query]);

  useEffect(() => {
    let active = true;
    setDetail(null);
    if (!selected) return;
    setError(null);
    void (async () => {
      try {
        const loaded = await api().crawlEntity!(selected.entity_id, selected.crawl_run_id);
        if (active) setDetail(loaded);
      } catch (err) {
        if (active) setError(message(err));
      }
    })();
    return () => {
      active = false;
    };
  }, [api, selected]);

  if (selected) {
    return (
      <div className="detail-section instances">
        <button className="link-button" onClick={() => setSelected(null)}>
          ← All found in documents
        </button>
        <h3 className="instances__entity">{selected.name}</h3>
        <div className="detail-meta">
          <span className="badge">{selected.class}</span>
        </div>
        <ul className="instances__keys" aria-label="Warehouse keys">
          {selected.keys.map((item) => (
            <li key={item}>
              <code>{item}</code>
            </li>
          ))}
        </ul>
        {error && <p className="instance-error" role="alert">{error}</p>}
        {!detail && !error && <p className="instance-empty">Loading documents…</p>}
        {detail && (
          <>
            {detail.related.length > 0 && (
              <>
                <h4>Related</h4>
                <ul className="instances__related">
                  {detail.related.map((item, index) => (
                    <li key={`${item.entity_id}-${index}`}>
                      <span className="instances__relationship">{item.relationship}</span>{" "}
                      <button
                        className="link-button"
                        title={`Show ${item.class} in the ontology`}
                        onClick={() => onSelectClass(item.class)}
                      >
                        {item.class}
                      </button>{" "}
                      {item.name}
                    </li>
                  ))}
                </ul>
              </>
            )}
            {detail.claims.length > 0 && (
              <>
                <h4>{plural(detail.claims_total, "claim")}</h4>
                <ul className="instances__related">
                  {detail.claims.map((claim, index) => (
                    <li key={`${claim.predicate}-${index}`}>
                      <span className="instances__relationship">
                        {claim.predicate.replace(/_/g, " ").toLowerCase()}
                      </span>
                      {claim.evidence[0] && <q>{claim.evidence[0].excerpt}</q>}
                    </li>
                  ))}
                </ul>
              </>
            )}
            <h4>
              {detail.documents.length < detail.documents_total
                ? `${detail.documents.length} of ${plural(detail.documents_total, "document")}`
                : plural(detail.documents_total, "document")}
            </h4>
            <ol className="instances__documents">
              {detail.documents.map((document) => (
                <Document key={document.asset_id} document={document} />
              ))}
            </ol>
          </>
        )}
      </div>
    );
  }

  return (
    <div className="detail-section instances">
      <h3>Found in documents</h3>
      <input
        className="instances__search"
        aria-label="Search found entities"
        placeholder="Search by name or key"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      {error && <p className="instance-error" role="alert">{error}</p>}
      {!found && !error && <p className="instance-empty">Loading…</p>}
      {found && found.total === 0 && (
        <p className="instance-empty">
          {query
            ? "Nothing found matches."
            : "The crawler has not found any of these in documents."}
        </p>
      )}
      {found && found.total > 0 && (
        <>
          <p className="instances__count">
            {found.entities.length < found.total
              ? `Showing ${found.entities.length} of ${found.total.toLocaleString()}, most-documented first`
              : `${found.total.toLocaleString()} found in the latest crawl`}
          </p>
          <ul className="instances__list">
            {found.entities.map((entity) => (
              <li key={`${entity.crawl_run_id}:${entity.entity_id}`}>
                <button className="link-button" onClick={() => setSelected(entity)}>
                  {entity.name}
                </button>
                <span className="instances__documents-count">
                  {classes.length > 1 ? `${entity.class} · ` : ""}
                  {plural(entity.documents ?? 0, "document")}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
