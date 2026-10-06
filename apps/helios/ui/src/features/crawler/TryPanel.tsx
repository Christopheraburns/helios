/** Shows what one rule of the settings being edited matches, in passages from
 * the latest crawl or in text the user pastes. Nothing is saved. */
import { Fragment, type ReactNode, useEffect, useState } from "react";

import type { CrawlerTryResult, HeliosApi } from "../../api/client";
import { whereInDocument } from "../../components/EvidenceResults";
import type { Settings, TryRule } from "./SettingsForm";

interface Props {
  api: () => Pick<HeliosApi, "tryCrawlerSettingsRule">;
  settings: Settings;
  rule: TryRule | null;
  title: string;
}

type Match = CrawlerTryResult["passages"][number]["matches"][number];

/** Passage text with each match marked; a blocked cue is struck through. */
export function Marked({ text, matches }: { text: string; matches: Match[] }) {
  const parts: ReactNode[] = [];
  let at = 0;
  [...matches]
    .sort((a, b) => a.start - b.start || b.end - a.end)
    .forEach((match, index) => {
      if (match.start < at || match.end > text.length) return; // overlaps the previous mark
      parts.push(<Fragment key={`t${index}`}>{text.slice(at, match.start)}</Fragment>);
      parts.push(
        <mark
          key={`m${index}`}
          className={match.blocked ? "try-blocked" : undefined}
          title={[
            match.rule ? `Rule: ${match.rule}` : "",
            match.strength ? `Strength: ${match.strength}` : "",
            match.blocked ? "Cancelled by a negation or hedge" : "",
          ]
            .filter(Boolean)
            .join(" · ")}
        >
          {text.slice(match.start, match.end)}
        </mark>,
      );
      at = match.end;
    });
  parts.push(<Fragment key="end">{text.slice(at)}</Fragment>);
  return <p className="try-passage__text">{parts}</p>;
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : "The rule could not be tried.";
}

export default function TryPanel({ api, settings, rule, title }: Props) {
  const [source, setSource] = useState<"crawl" | "text">("crawl");
  const [text, setText] = useState("");
  const [result, setResult] = useState<CrawlerTryResult | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ruleKey = rule ? JSON.stringify(rule) : "";
  const settingsKey = JSON.stringify(settings);

  useEffect(() => {
    if (!rule) return;
    if (source === "text" && !text.trim()) {
      setResult(null);
      setProblem(null);
      return;
    }
    let active = true;
    setBusy(true);
    // Wait for a pause in typing: every keystroke in the form changes the settings.
    const timer = setTimeout(async () => {
      try {
        const client = api();
        if (!client.tryCrawlerSettingsRule) throw new Error("The crawler API is unavailable.");
        const found = await client.tryCrawlerSettingsRule(settings, { ...rule }, source === "text" ? text : undefined);
        if (!active) return;
        setResult(found);
        setProblem(null);
      } catch (err) {
        if (!active) return;
        setResult(null);
        setProblem(message(err));
      } finally {
        if (active) setBusy(false);
      }
    }, 500);
    return () => {
      active = false;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed by content, not identity
  }, [api, ruleKey, settingsKey, source, text]);

  if (!rule) {
    return (
      <aside className="try-panel" aria-label="Try it">
        <h3>Try it</h3>
        <p className="muted">
          Press <strong>Try</strong> on a pattern, a label, a set of phrases or a claim to see what
          it matches in real text, before you save anything.
        </p>
      </aside>
    );
  }

  const noSample = result?.sample.kind === "none";
  return (
    <aside className="try-panel" aria-label="Try it">
      <h3>Try it: {title}</h3>
      <div className="try-source" role="radiogroup" aria-label="Text to try it on">
        <label>
          <input type="radio" name="try-source" checked={source === "crawl"} onChange={() => setSource("crawl")} /> Latest crawl
        </label>
        <label>
          <input type="radio" name="try-source" checked={source === "text"} onChange={() => setSource("text")} /> Text I paste
        </label>
      </div>
      {source === "text" && (
        <textarea
          className="try-text"
          aria-label="Text to try the rule on"
          placeholder="Paste an email, a chat message or a page of a report"
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
      )}
      {problem && (
        <pre className="crawler-error" role="alert">
          {problem}
        </pre>
      )}
      {busy && !result && <p className="muted">Trying…</p>}
      {noSample && result?.sample.kind === "none" && (
        <div className="try-empty">
          {result.sample.reason}
          <div>
            <button type="button" className="crawler-button" onClick={() => setSource("text")}>
              Paste text instead
            </button>
          </div>
        </div>
      )}
      {result && !noSample && (
        <>
          <p className="try-summary" aria-live="polite">
            {result.matches === 0 ? (
              <>
                <strong>No match</strong> in {result.scanned.toLocaleString()}{" "}
                {result.scanned === 1 ? "passage" : "passages"}.
              </>
            ) : (
              <>
                <strong>{result.matches.toLocaleString()}</strong> {result.matches === 1 ? "match" : "matches"} in{" "}
                {result.matched.toLocaleString()} of {result.scanned.toLocaleString()}{" "}
                {result.scanned === 1 ? "passage" : "passages"}
                {result.blocked > 0 && <>; {result.blocked.toLocaleString()} cancelled by a negation or hedge</>}.
              </>
            )}
            {busy && <span className="muted"> Updating…</span>}
          </p>
          {result.values.length > 0 && (
            <div className="try-values">
              <span className="muted">
                Most often matched{result.distinct_values > result.values.length ? ` (of ${result.distinct_values.toLocaleString()} different)` : ""}:
              </span>
              <ul>
                {result.values.map((value) => (
                  <li key={value.text}>
                    <code>{value.text}</code> <span className="muted">×{value.count}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <ol className="try-passages">
            {result.passages.map((passage, index) => (
              <li key={index} className="try-passage">
                <div className="try-passage__where">{whereInDocument(passage.locator, passage.segment_type)}</div>
                <Marked text={passage.text} matches={passage.matches} />
                {passage.truncated && <div className="muted">… (long passage, start shown)</div>}
              </li>
            ))}
          </ol>
          {result.matched > result.passages.length && (
            <p className="muted">Showing the first {result.passages.length} passages with a match.</p>
          )}
        </>
      )}
    </aside>
  );
}
