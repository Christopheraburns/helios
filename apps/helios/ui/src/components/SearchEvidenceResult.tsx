/**
 * CR-11: Display search_evidence results in conversation.
 * Shows document segments with locators and relevance scores.
 */

interface Segment {
  segment_id: string;
  asset_id: string;
  text: string;
  locators: Record<string, unknown>;
  relevance: number;
}

interface SearchEvidenceResult {
  segments?: Segment[];
  query?: string;
  count?: number;
  error?: string;
}

function formatLocator(locators: Record<string, unknown>): string {
  const page = locators.page;
  if (page !== undefined && typeof page === "number") return `Page ${page + 1}`;
  const part = locators.part;
  if (part) return `Email: ${part}`;
  const messageId = locators.message_id;
  if (messageId) return `Message ${String(messageId).slice(0, 8)}`;
  return "Document";
}

function SearchEvidenceResult({ result }: { result: SearchEvidenceResult }) {
  if (!result.segments || result.segments.length === 0) {
    return (
      <section className="talk-result" aria-label="No evidence found">
        <div className="talk-result__header">
          <h4>Evidence</h4>
        </div>
        <p className="muted">No matching document segments found for "{result.query}".</p>
      </section>
    );
  }

  return (
    <section className="talk-result talk-result--evidence" aria-label="Evidence segments">
      <div className="talk-result__header">
        <h4>Evidence</h4>
        <span>
          {result.count || result.segments.length} segment
          {(result.count || result.segments.length) === 1 ? "" : "s"}
        </span>
      </div>
      <div className="talk-segments">
        {result.segments.map((segment) => (
          <article
            key={segment.segment_id}
            className="talk-segment"
            title={`Relevance: ${(segment.relevance * 100).toFixed(0)}%`}
          >
            <div className="talk-segment__header">
              <span className="talk-segment__locator">
                {formatLocator(segment.locators)}
              </span>
              <span className="talk-segment__relevance">
                {(segment.relevance * 100).toFixed(0)}% match
              </span>
            </div>
            <p className="talk-segment__text">{segment.text}</p>
            <span className="talk-segment__id" title={segment.asset_id}>
              {segment.asset_id.slice(0, 13)}
            </span>
          </article>
        ))}
      </div>
    </section>
  );
}

export default SearchEvidenceResult;
