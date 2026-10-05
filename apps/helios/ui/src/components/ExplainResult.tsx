/**
 * CR-11: Display explain results in conversation.
 * Shows what documents say about an entity: claims with supporting evidence.
 */

interface Evidence {
  segment_id: string;
  text: string;
  locators?: Record<string, unknown>;
}

interface Claim {
  predicate: string;
  object?: string;
  evidence: Evidence[];
}

interface ExplainResult {
  entity?: {
    key: string;
    class?: string;
  };
  mentions_count?: number;
  claims_count?: number;
  claims?: Claim[];
  error?: string;
}

function formatLocator(locators?: Record<string, unknown>): string {
  if (!locators) return "Source";
  const page = locators.page;
  if (page !== undefined && typeof page === "number") return `Page ${page + 1}`;
  const part = locators.part;
  if (part) return `Email: ${part}`;
  const messageId = locators.message_id;
  if (messageId) return `Message ${String(messageId).slice(0, 8)}`;
  return "Document";
}

function ExplainResult({ result }: { result: ExplainResult }) {
  if (!result.entity) {
    return (
      <section className="talk-result" aria-label="Entity explanation">
        <div className="talk-result__header">
          <h4>Entity Details</h4>
        </div>
        <p className="muted">No information found about this entity.</p>
      </section>
    );
  }

  const { entity, mentions_count = 0, claims_count = 0, claims = [] } = result;

  return (
    <section className="talk-result talk-result--explain" aria-label="Entity explanation">
      <div className="talk-result__header">
        <h4>
          {entity.class ? `${entity.class}: ` : ""}
          <code>{entity.key}</code>
        </h4>
      </div>

      <div className="talk-entity-summary">
        <span>
          <strong>{mentions_count}</strong> mention{mentions_count === 1 ? "" : "s"}
        </span>
        <span>
          <strong>{claims_count}</strong> claim{claims_count === 1 ? "" : "s"}
        </span>
      </div>

      {claims.length === 0 ? (
        <p className="muted">No claims found about this entity.</p>
      ) : (
        <div className="talk-claims">
          {claims.map((claim, index) => (
            <details key={`claim-${index}`} className="talk-claim">
              <summary className="talk-claim__summary">
                <strong>{claim.predicate}</strong>
                {claim.object && <span className="muted"> → {claim.object}</span>}
                <span className="talk-claim__count">
                  {claim.evidence.length} piece{claim.evidence.length === 1 ? "" : "s"} of evidence
                </span>
              </summary>

              <div className="talk-claim__evidence">
                {claim.evidence.map((ev, evIndex) => (
                  <article key={`evidence-${index}-${evIndex}`} className="talk-evidence">
                    <span className="talk-evidence__locator">
                      {formatLocator(ev.locators)}
                    </span>
                    <p className="talk-evidence__text">{ev.text}</p>
                  </article>
                ))}
              </div>
            </details>
          ))}
        </div>
      )}
    </section>
  );
}

export default ExplainResult;
