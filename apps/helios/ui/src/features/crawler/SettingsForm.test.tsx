import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import type { CrawlerTryResult } from "../../api/client";
import SettingsForm, { setAt, type Settings, type TryRule } from "./SettingsForm";
import TryPanel, { Marked } from "./TryPanel";

const SETTINGS: Settings = {
  schema_version: "2",
  patterns: [
    { name: "case_number", regex: "CS-\\d{6}", group: 0, kind: "document_id", proposed_class: "Return", columns: [], ignore_case: false, key_name: "case" },
  ],
  pdf_labels: [{ label: "Customer ID", kind: "key", proposed_class: "Customer", columns: ["c_customer_id"] }],
  class_cues: { Store: ["\\bstore\\b"] },
  contextual: { Item: ["the item"] },
  cases: { identifiers: ["case_number"], date_window_days: 30 },
  claims: { cues: { REFUND_APPROVED: ["refund approved"] }, negations: ["not"], hedges: ["if"] },
  dictionary: { ordinary_words: ["the", "store"], short_token: 5 },
  llm: { instructions: "Read.", prompt_version: "llm-1" },
};
const VOCABULARY = { classes: ["Customer", "Item", "Return", "Store"], predicates: ["REFUND_APPROVED", "REFUND_REQUESTED"] };

function Harness({ onTry = vi.fn(), seen }: { onTry?: (rule: TryRule, title: string) => void; seen: { current: Settings } }) {
  const [settings, setSettings] = useState(SETTINGS);
  seen.current = settings;
  return <SettingsForm settings={settings} vocabulary={VOCABULARY} onChange={setSettings} onTry={onTry} />;
}

describe("SettingsForm", () => {
  it("edits patterns as fields and reports the whole document", () => {
    const seen = { current: SETTINGS };
    const onTry = vi.fn();
    render(<Harness seen={seen} onTry={onTry} />);

    fireEvent.change(screen.getByLabelText("Pattern 1 expression"), { target: { value: "CS-\\d{7}" } });
    expect(seen.current.patterns[0].regex).toBe("CS-\\d{7}");
    expect(seen.current.claims).toBe(SETTINGS.claims); // untouched sections are the same objects

    fireEvent.click(screen.getAllByRole("button", { name: "Try" })[0]);
    expect(onTry).toHaveBeenCalledWith({ type: "pattern", name: "case_number" }, "Pattern “case_number”");

    fireEvent.click(screen.getByRole("button", { name: "Add a pattern" }));
    expect(seen.current.patterns).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Remove pattern pattern_2" }));
    expect(seen.current.patterns.map((p: Settings) => p.name)).toEqual(["case_number"]);
    // A key pattern asks for warehouse columns; a document ID asks what it is stored as.
    expect(screen.getByDisplayValue("case")).toBeInTheDocument();
    expect(screen.queryByLabelText("Add to pattern 1 columns")).not.toBeInTheDocument();
  });

  it("edits phrase lists as chips, per class and per claim", () => {
    const seen = { current: SETTINGS };
    const onTry = vi.fn();
    render(<Harness seen={seen} onTry={onTry} />);

    fireEvent.click(screen.getByRole("tab", { name: "Claims" }));
    const add = screen.getByLabelText("Add to Cue phrases per claim for REFUND_APPROVED");
    fireEvent.change(add, { target: { value: "refund * approved" } });
    fireEvent.keyDown(add, { key: "Enter" });
    expect(seen.current.claims.cues.REFUND_APPROVED).toEqual(["refund approved", "refund * approved"]);
    fireEvent.click(screen.getByRole("button", { name: "Remove refund approved from Cue phrases per claim for REFUND_APPROVED" }));
    expect(seen.current.claims.cues.REFUND_APPROVED).toEqual(["refund * approved"]);
    fireEvent.click(screen.getByRole("button", { name: "Try" }));
    expect(onTry).toHaveBeenCalledWith({ type: "claim", predicate: "REFUND_APPROVED" }, "Claim refund approved");

    const fresh = screen.getByLabelText("New claim type");
    fireEvent.change(fresh, { target: { value: "REFUND_REQUESTED" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Add" })[1]); // the cue-phrase group's Add
    expect(Object.keys(seen.current.claims.cues)).toEqual(["REFUND_APPROVED", "REFUND_REQUESTED"]);

    fireEvent.click(screen.getByRole("tab", { name: "Names" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove store from ordinary words" }));
    expect(seen.current.dictionary.ordinary_words).toEqual(["the"]);
    fireEvent.click(screen.getByRole("button", { name: "Remove class Store" }));
    expect(seen.current.class_cues).toEqual({});
  });

  it("defines a kind of claim: its subject, its object and where each is looked for", () => {
    const seen = { current: SETTINGS };
    render(<Harness seen={seen} />);
    fireEvent.click(screen.getByRole("tab", { name: "Claims" }));

    fireEvent.change(screen.getByLabelText("New kind of claim"), { target: { value: "REFUND_REQUESTED" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Add" })[0]);
    expect(seen.current.claims.predicates.REFUND_REQUESTED.subject.find).toEqual(["in_unit", "single_in_document"]);

    fireEvent.change(screen.getByLabelText("REFUND_REQUESTED subject class"), { target: { value: "Customer" } });
    const find = screen.getByLabelText("Add to REFUND_REQUESTED subject find");
    fireEvent.change(find, { target: { value: "case_edge:PartyTo" } });
    fireEvent.keyDown(find, { key: "Enter" });
    expect(seen.current.claims.predicates.REFUND_REQUESTED.subject).toEqual({
      class: "Customer",
      find: ["in_unit", "single_in_document", { case_edge: "PartyTo" }],
    });
    expect(screen.getByText("case_edge:PartyTo", { selector: ".settings-chip" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Add a speaker rule" }));
    fireEvent.change(screen.getByLabelText("Speaker rule 1 speaker"), { target: { value: "customer" } });
    expect(seen.current.claims.speakers).toEqual([
      { segment: "message", field: null, values: [], author_class: null, speaker: "customer" },
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Remove claim REFUND_REQUESTED" }));
    expect(seen.current.claims.predicates).toEqual({});
  });

  it("describes a chat export format by where its fields are", () => {
    const seen = { current: SETTINGS };
    render(<Harness seen={seen} />);
    fireEvent.click(screen.getByRole("tab", { name: "Documents" }));
    expect(screen.getByText(/chat files will not be read/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Add a chat format" }));
    fireEvent.change(screen.getByLabelText("Chat format 1 List of messages"), { target: { value: "data.events" } });
    fireEvent.change(screen.getByLabelText("Chat format 1 Message: sender’s name"), { target: { value: "" } });
    const match = screen.getByLabelText("Add to chat format 1 match");
    fireEvent.change(match, { target: { value: "export.kind=support-desk" } });
    fireEvent.keyDown(match, { key: "Enter" });
    const layout = seen.current.analyzers.chat.layouts[0];
    expect(layout.messages).toBe("data.events");
    expect(layout.sender_name).toBeNull(); // cleared: not recorded
    expect(layout.match).toEqual({ "export.kind": "support-desk" });
    expect(layout.participant_id).toBe("sender");

    const values = screen.getByLabelText("Add to pdf value patterns");
    fireEvent.change(values, { target: { value: "^[A-Z]{2}-\\d+$" } });
    fireEvent.keyDown(values, { key: "Enter" });
    expect(seen.current.analyzers.pdf.value_patterns).toEqual(["^[A-Z]{2}-\\d+$"]);
  });

  it("links cases by ticking identifier patterns", () => {
    const seen = { current: SETTINGS };
    render(<Harness seen={seen} />);
    fireEvent.click(screen.getByRole("tab", { name: "Cases" }));
    const box = screen.getByRole("checkbox", { name: "case_number" });
    expect(box).toBeChecked();
    fireEvent.click(box);
    expect(seen.current.cases.identifiers).toEqual([]);
  });

  it("replaces a value deep in the document without changing the rest", () => {
    const next = setAt(SETTINGS, ["patterns", 0, "group"], 1);
    expect(next.patterns[0].group).toBe(1);
    expect(SETTINGS.patterns[0].group).toBe(0);
    expect(next.pdf_labels).toBe(SETTINGS.pdf_labels);
    expect(setAt({}, ["analyzers", "pdf", "enabled"], false)).toEqual({ analyzers: { pdf: { enabled: false } } });
  });
});

const RESULT: CrawlerTryResult = {
  sample: { kind: "crawl", crawl_runs: [{ crawl_run_id: "crawl_1", source: "ds", started_at: "2026-10-06T00:00:00Z" }] },
  scanned: 1003,
  matched: 2,
  matches: 3,
  blocked: 1,
  values: [{ text: "refund approved", count: 2 }],
  distinct_values: 1,
  passages: [
    {
      segment_type: "email_body",
      locator: { part: "body" },
      text: "The refund was not approved. Later: refund approved.",
      truncated: false,
      matches: [
        { start: 4, end: 27, text: "refund was not approved", rule: "refund * approved", strength: "strong", blocked: true },
        { start: 36, end: 51, text: "refund approved", rule: "refund approved", strength: "strong", blocked: false },
      ],
    },
  ],
};

describe("TryPanel", () => {
  it("explains itself until a rule is chosen", () => {
    render(<TryPanel api={() => ({})} settings={SETTINGS} rule={null} title="" />);
    expect(screen.getByText(/to see what\s+it matches in real text/)).toBeInTheDocument();
  });

  it("shows what a rule matches in the latest crawl, and what was cancelled", async () => {
    const tryCrawlerSettingsRule = vi.fn().mockResolvedValue(RESULT);
    const api = () => ({ tryCrawlerSettingsRule });
    render(<TryPanel api={api} settings={SETTINGS} rule={{ type: "claim", predicate: "REFUND_APPROVED" }} title="Claim refund approved" />);

    const summary = await screen.findByText(/in\s+2 of 1,003/, {}, { timeout: 3000 });
    expect(summary).toHaveTextContent("3 matches in 2 of 1,003 passages; 1 cancelled by a negation or hedge.");
    expect(tryCrawlerSettingsRule).toHaveBeenCalledWith(SETTINGS, { type: "claim", predicate: "REFUND_APPROVED" }, undefined);
    const marks = screen.getAllByText(/refund.*approved/, { selector: "mark" });
    expect(marks.map((m) => [m.textContent, m.className])).toEqual([
      ["refund was not approved", "try-blocked"],
      ["refund approved", ""],
    ]);
    expect(screen.getByText("×2")).toBeInTheDocument(); // "refund approved" matched twice
  });

  it("offers pasted text when nothing has been crawled, and tries the rule on it", async () => {
    const tryCrawlerSettingsRule = vi
      .fn()
      .mockResolvedValueOnce({ ...RESULT, sample: { kind: "none", reason: "No crawl has finished yet. Paste some text." }, scanned: 0, matched: 0, matches: 0, blocked: 0, values: [], passages: [] })
      .mockResolvedValue({ ...RESULT, sample: { kind: "text" }, scanned: 1, matched: 1, matches: 1, blocked: 0 });
    const api = () => ({ tryCrawlerSettingsRule });
    render(<TryPanel api={api} settings={SETTINGS} rule={{ type: "pattern", name: "case_number" }} title="Pattern" />);

    expect(await screen.findByText(/No crawl has finished yet/, {}, { timeout: 3000 })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Paste text instead" }));
    fireEvent.change(screen.getByLabelText("Text to try the rule on"), { target: { value: "Case CS-123456" } });
    await waitFor(
      () => expect(tryCrawlerSettingsRule).toHaveBeenLastCalledWith(SETTINGS, { type: "pattern", name: "case_number" }, "Case CS-123456"),
      { timeout: 3000 },
    );
    expect(await screen.findByText(/in\s+1 of 1 passage/)).toBeInTheDocument();
  });

  it("marks matches and skips ones that overlap or run past the text", () => {
    const { container } = render(
      <Marked
        text="RMA-1 and RMA-2"
        matches={[
          { start: 0, end: 5, text: "RMA-1" },
          { start: 2, end: 5, text: "A-1" },
          { start: 10, end: 15, text: "RMA-2" },
          { start: 40, end: 45, text: "?" },
        ]}
      />,
    );
    expect([...container.querySelectorAll("mark")].map((m) => m.textContent)).toEqual(["RMA-1", "RMA-2"]);
    expect(container.textContent).toBe("RMA-1 and RMA-2");
  });
});
