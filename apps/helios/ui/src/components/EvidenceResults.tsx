/** What the document tools returned in a conversation turn: passages found by
 * search_evidence and claims found by entity_claims (CR-11). */

export interface LinkedEntity {
  entity_id: string;
  class: string;
  name: string;
  keys?: string[];
}

export interface EvidenceSegment {
  segment_id: string;
  asset_id: string;
  segment_type: string;
  locator: Record<string, unknown>;
  text: string;
  entities: LinkedEntity[];
  relevance: number;
}

export interface EvidenceSearch {
  query: string;
  count: number;
  segments: EvidenceSegment[];
}

export interface EntityClaim {
  predicate: string;
  subject: { name: string; class: string } | null;
  object: { name: string; class: string } | null;
  object_value: string | null;
  confidence: number;
  evidence_count: number;
  evidence: { asset_id: string; locator: Record<string, unknown>; excerpt: string }[];
}

export interface EntityClaims {
  entity: string;
  entities: (LinkedEntity & { source: string })[];
  claims_total: number;
  claims: EntityClaim[];
}

const SEGMENT_TYPES: Record<string, string> = {
  page: "PDF",
  email: "Email",
  chat: "Chat excerpt",
  email_header: "Email sender",
  email_subject: "Email subject",
  email_body: "Email",
  message: "Chat message",
};

/** Where in its document a passage is, in words. */
export function whereInDocument(locator: Record<string, unknown>, segmentType?: string): string {
  const kind = segmentType ? (SEGMENT_TYPES[segmentType] ?? segmentType) : "";
  if (typeof locator.page === "number") return `PDF, page ${locator.page}`;
  if (typeof locator.message_id === "string") return kind || "Chat message";
  if (typeof locator.part === "string") return kind || `Email ${locator.part}`;
  if (typeof locator.header === "string") return `Email ${locator.header} line`;
  return kind || "Document";
}

const plural = (n: number, word: string) => `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;

export function EvidenceSearchResult({ result }: { result: EvidenceSearch }) {
  return (
    <section className="talk-result talk-evidence" aria-label="Passages from documents">
      <div className="talk-result__header">
        <h4>From documents</h4>
        <span>
          {plural(result.segments.length, "passage")} for “{result.query}”
        </span>
      </div>
      {result.segments.length === 0 ? (
        <p className="talk-evidence__empty">No passage in the crawled documents matched.</p>
      ) : (
        <ol className="talk-evidence__list">
          {result.segments.map((segment) => (
            <li key={segment.segment_id} className="talk-evidence__item">
              <div className="talk-evidence__meta">
                <span>{whereInDocument(segment.locator, segment.segment_type)}</span>
                <span title="How close the passage is in meaning to the search (100% is identical)">
                  {Math.round(Math.max(0, segment.relevance) * 100)}% match
                </span>
              </div>
              <p className="talk-evidence__text">{segment.text}</p>
              {segment.entities.length ? (
                <ul className="talk-evidence__entities" aria-label="Linked to">
                  {segment.entities.map((entity) => (
                    <li key={entity.entity_id} title={(entity.keys ?? []).join("\n")}>
                      <span>{entity.class}</span> {entity.name}
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export function EntityClaimsResult({ result }: { result: EntityClaims }) {
  const names = result.entities.map((entity) => `${entity.class} ${entity.name}`).join(", ");
  return (
    <section className="talk-result talk-evidence" aria-label="Claims from documents">
      <div className="talk-result__header">
        <h4>What documents say{names ? ` about ${names}` : ""}</h4>
        <span>
          {result.claims.length < result.claims_total
            ? `${result.claims.length} of ${plural(result.claims_total, "claim")}`
            : plural(result.claims_total, "claim")}
        </span>
      </div>
      {result.entities.length === 0 ? (
        <p className="talk-evidence__empty">
          No entity matching “{result.entity}” was found in the crawled documents.
        </p>
      ) : result.claims.length === 0 ? (
        <p className="talk-evidence__empty">The crawled documents make no claims about it.</p>
      ) : (
        <ol className="talk-evidence__list">
          {result.claims.map((claim, index) => (
            <li key={`${claim.predicate}-${index}`} className="talk-evidence__item">
              <div className="talk-evidence__meta">
                <strong>{claim.predicate.replace(/_/g, " ").toLowerCase()}</strong>
                <span>{plural(claim.evidence_count, "passage")}</span>
              </div>
              <p className="talk-evidence__claim">
                {claim.subject ? `${claim.subject.class} ${claim.subject.name}` : "Unknown"}
                {claim.object || claim.object_value
                  ? ` → ${claim.object ? `${claim.object.class} ${claim.object.name}` : claim.object_value}`
                  : ""}
              </p>
              {claim.evidence.map((passage, passageIndex) => (
                <blockquote key={passageIndex} className="talk-evidence__quote">
                  {passage.excerpt}
                  <footer>{whereInDocument(passage.locator)}</footer>
                </blockquote>
              ))}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
