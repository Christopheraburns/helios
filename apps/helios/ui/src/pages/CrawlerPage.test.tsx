import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ApiUnavailableError, AuthorizationError, type HeliosApi } from "../api/client";
import type { ApplicationContextState } from "../hooks/useApplicationContext";
import CrawlerPage from "./CrawlerPage";

const RUN = {
  crawl_run_id: "crawl_abc",
  connector: "helios_ds_s3",
  source: "1ca99f86-e6a0-57fc-8311-cadcac4c8302",
  status: "SUCCEEDED",
  started_at: "2026-10-01T12:00:00+00:00",
  finished_at: "2026-10-01T12:00:25+00:00",
  actor: "srv_helios_crawler",
  ontology_version: "0.2.0",
  crawler_version: "0.1.0",
  settings_version: null,
  settings_hash: "24ab14cd820a",
  settings: { dataset_id: "1ca99f86", full: false },
  counts: { listed: 301, fetched: 293, fetch_failed: 8 },
  error: null,
  latest_evaluation: null,
};

const SUMMARY = {
  segments_coverage: 1,
  mention_recall_direct: 0.9512,
  mention_recall_alias: 1,
  mention_recall_contextual: 1,
  mention_precision: 0.7417,
  resolution_accuracy_alias: 0,
  sameas_precision: null,
  claim_precision: null,
  claim_recall: 0,
  evidence_agreement: null,
  questions_pass_rate: 0.1667,
};

const EVALUATION = {
  evaluation_id: "eval-1",
  crawl_run_id: "crawl_abc",
  dataset_id: "1ca99f86-e6a0-57fc-8311-cadcac4c8302",
  evaluated_at: "2026-10-02T09:00:00+00:00",
  evaluator: "cloudera-workbench:alice",
  evaluator_mode: "proxy",
  harness_version: "0.1.0",
  ontology_version: "0.2.0",
  strategy: "deterministic",
  status: "SUCCEEDED",
  error: null,
  summary: SUMMARY,
  metrics: {
    mentions: {
      by_class_tier: {
        Customer: { direct: { truth_total: 527, truth_found: 455, recall: 0.8634, class_correct_rate: 0.99 } },
      },
      precision_by: { "extractor:pattern": { crawler_total: 2488, crawler_matched: 1436, precision: 0.5772 } },
    },
    resolution: { by_tier: { alias: { found: 2, correct: 0, wrong: 0, possibly_only: 0, unresolved: 2, accuracy: 0 } } },
    claims: { by_predicate: {} },
    relationships: { by_type: {} },
    questions: { by_kind: { no_answer: { total: 10, passed: 10, pass_rate: 1 } } },
    cases: { skipped: true },
    diagnostics: {
      mentions: {
        unmatched_crawler_mentions: [{ extractor: "pattern", class: "?", count: 920 }],
        unfound_truth_surfaces: [{ class: "Customer", tier: "direct", surface: "angela", count: 2 }],
      },
    },
  },
  run: {
    started_at: RUN.started_at,
    finished_at: RUN.finished_at,
    status: "SUCCEEDED",
    source: RUN.source,
    crawler_version: "0.3.0",
    settings_version: 2,
    ontology_version: "0.2.0",
    counts: RUN.counts,
  },
};

function renderPage(api: Partial<HeliosApi>, tab = "runs") {
  const context = {
    crawlerClient: () => api as HeliosApi,
  } as unknown as ApplicationContextState;
  return render(
    <MemoryRouter initialEntries={[`/crawler?tab=${tab}`]}>
      <CrawlerPage context={context} />
    </MemoryRouter>,
  );
}

describe("CrawlerPage", () => {
  it("lists runs and shows a run's assets with their problems", async () => {
    const api = {
      crawlRuns: vi.fn().mockResolvedValue([RUN]),
      crawlerSettings: vi.fn(),
      crawlRun: vi.fn().mockResolvedValue({
        ...RUN,
        asset_counts: { by_status: { fetched: 1, fetch_failed: 1 }, by_class: { Document: 2 } },
        assets: [
          { asset_id: "a1", asset_version_id: "v", ontology_class: "Document", mime_type: "application/pdf",
            status: "fetched", status_detail: "", size_bytes: 3134, semantic_timestamp: "2001-06-14", object_key: "k/a1" },
          { asset_id: "a2", asset_version_id: "v", ontology_class: "Document", mime_type: "application/pdf",
            status: "fetch_failed", status_detail: "ProtocolError: RAZ", size_bytes: 10, semantic_timestamp: null, object_key: "k/a2" },
        ],
      }),
    };
    renderPage(api);
    const cells = await screen.findAllByText("1ca99f86-e6a0…"); // dropdown option + table cell
    const cell = cells.find((el) => el.tagName === "TD")!;
    expect(screen.getByText("8")).toBeTruthy(); // problems column
    fireEvent.click(cell);
    expect(await screen.findByText("ProtocolError: RAZ")).toBeTruthy();
    fireEvent.click(screen.getByText("fetch_failed: 1"));
    await waitFor(() => expect(screen.queryByText("a1")).toBeNull());
    expect(api.crawlRun).toHaveBeenCalledWith("crawl_abc");
  });

  it("counts unreadable documents as problems, not what the analysis found", async () => {
    const counts = { listed: 301, analyzed: 299, no_text: 2, segments: 1003, mentions: 4297, claims: 403, claims_unanchored: 217 };
    renderPage({
      crawlRuns: vi.fn().mockResolvedValue([{ ...RUN, counts }]),
      crawlRun: vi.fn().mockResolvedValue({ ...RUN, counts, asset_counts: { by_status: {}, by_class: {} }, assets: [] }),
    });
    const row = (await screen.findByText("SUCCEEDED")).closest("tr")!;
    expect(row.querySelector(".crawler-problem")).toHaveTextContent(/^2$/);
    fireEvent.click(row);
    expect(await screen.findByText("1,003 segments · 4,297 mentions · 403 claims")).toBeInTheDocument();
    expect(screen.getByText("2 no text")).toBeInTheDocument();
  });

  it("starts a crawl through the API and shows it in progress", async () => {
    const target = { kind: "dataset", id: RUN.source, label: "Helios-DS dataset", connector: "helios_ds" };
    const launch = {
      job_run_id: "run-9",
      status: "ENGINE_SCHEDULING",
      active: true,
      created_at: "2026-10-05T16:00:00+00:00",
      finished_at: null,
      source_id: null,
      dataset_id: RUN.source,
      full: false,
      requested_by: "cloudera-workbench:alice",
    };
    const crawlLaunches = vi
      .fn()
      .mockResolvedValueOnce({ available: true, reason: null, job_name: "helios-crawl", launches: [] })
      .mockResolvedValue({ available: true, reason: null, job_name: "helios-crawl", launches: [launch] });
    const startCrawl = vi.fn().mockResolvedValue(launch);
    renderPage({
      crawlRuns: vi.fn().mockResolvedValue([{ ...RUN, actor: "cburns", isolated: false, note: "baseline run" }]),
      crawlTargets: vi.fn().mockResolvedValue([target]),
      crawlLaunches,
      startCrawl,
    });

    expect(await screen.findByText("not isolated")).toBeInTheDocument();
    expect(screen.getByText("baseline run")).toBeInTheDocument();
    const button = await screen.findByRole("button", { name: "Start crawl" });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.change(screen.getByLabelText("Note for this crawl"), { target: { value: " after the pattern fix " } });
    fireEvent.change(screen.getByLabelText("Crawl strategy"), { target: { value: "llm" } });
    fireEvent.click(button);

    await waitFor(() =>
      expect(startCrawl).toHaveBeenCalledWith(target, false, "after the pattern fix", "llm"),
    );
    expect(await screen.findByText(/job run run-9\)\. It appears below/)).toBeInTheDocument();
    expect(await screen.findByText("SCHEDULING")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Start crawl" })).toBeDisabled());
  });

  it("says why crawls cannot be started when Workbench is unavailable", async () => {
    renderPage({
      crawlRuns: vi.fn().mockResolvedValue([RUN]),
      crawlTargets: vi.fn().mockResolvedValue([]),
      crawlLaunches: vi.fn().mockResolvedValue({ available: false, reason: "no Workbench project", launches: [] }),
    });
    expect(await screen.findByText(/Crawls can't be started from here: no Workbench project/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start crawl" })).not.toBeInTheDocument();
  });

  it("shows the latest score per run and evaluates a run against its dataset", async () => {
    const api = {
      crawlRuns: vi.fn().mockResolvedValue([{ ...RUN, latest_evaluation: { ...EVALUATION, metrics: undefined } }]),
      crawlRun: vi.fn().mockResolvedValue({
        ...RUN,
        latest_evaluation: null,
        asset_counts: { by_status: { fetched: 1 }, by_class: { Document: 1 } },
        assets: [],
      }),
      evaluateCrawlRun: vi
        .fn()
        .mockRejectedValueOnce(new AuthorizationError("You do not have access to Helios resources."))
        .mockResolvedValueOnce(EVALUATION),
    };
    renderPage(api);
    expect(await screen.findByText("direct 0.95 · claims 0.00")).toBeTruthy(); // Score column
    const cells = await screen.findAllByText("1ca99f86-e6a0…");
    fireEvent.click(cells.find((el) => el.tagName === "TD")!);
    const input = (await screen.findByLabelText("Ground-truth dataset")) as HTMLInputElement;
    expect(input.value).toBe(RUN.source); // defaults to the run's dataset for helios_ds connectors
    fireEvent.click(screen.getByText("Evaluate"));
    expect(await screen.findByText("You don't have access to the ground truth for this dataset.")).toBeTruthy();
    fireEvent.click(screen.getByText("Evaluate"));
    expect(await screen.findByText(/Scored .* by cloudera-workbench:alice \(proxy\)/)).toBeTruthy();
    expect(api.evaluateCrawlRun).toHaveBeenCalledWith("crawl_abc", RUN.source);
  });

  it("lists evaluations per dataset on the Scores tab and expands one", async () => {
    const api = {
      crawlRuns: vi.fn(),
      crawlerEvaluations: vi.fn().mockResolvedValue([
        EVALUATION,
        { ...EVALUATION, evaluation_id: "eval-2", dataset_id: "other-dataset", strategy: "llm" },
      ]),
    };
    renderPage(api, "scores");
    expect(await screen.findByText("deterministic")).toBeTruthy();
    expect(screen.queryByText("llm")).toBeNull(); // the other dataset is not selected
    expect(screen.getByText("0.95")).toBeTruthy();
    fireEvent.click(screen.getByText("deterministic"));
    expect(await screen.findByText("Customer / direct")).toBeTruthy();
    expect(screen.getByText("pattern / ?: 920")).toBeTruthy();
    expect(screen.getByText("angela")).toBeTruthy();
    expect(screen.getByText(/harness 0.1.0/)).toBeTruthy();
  });

  it("shows validation problems when saving settings fails", async () => {
    const api = {
      crawlRuns: vi.fn(),
      crawlerSettings: vi.fn().mockResolvedValue({
        active_version: null,
        using_defaults: true,
        content_hash: "24ab14cd820a",
        settings: { schema_version: "1" },
        versions: [],
      }),
      saveCrawlerSettings: vi
        .fn()
        .mockRejectedValue(new ApiUnavailableError("invalid settings\n• patterns.0.regex: not a valid regular expression")),
    };
    renderPage(api, "settings");
    expect(await screen.findByText("no saved settings")).toBeTruthy();
    fireEvent.click(screen.getByText("Validate and save"));
    expect(await screen.findByText(/not a valid regular expression/)).toBeTruthy();

    fireEvent.click(screen.getByRole("radio", { name: "JSON" }));
    fireEvent.change(screen.getByLabelText("Crawler settings JSON"), { target: { value: "{ not json" } });
    fireEvent.click(screen.getByText("Validate and save"));
    expect(await screen.findByText(/Not valid JSON/)).toBeTruthy();
    // The form cannot show unreadable JSON, and says so.
    fireEvent.click(screen.getByRole("radio", { name: "Form" }));
    expect(screen.getByText(/The JSON cannot be read/)).toBeInTheDocument();
  });

  it("labels LLM crawls, filters by strategy and shows the model and its usage", async () => {
    const llmRun = {
      ...RUN,
      crawl_run_id: "crawl_llm",
      strategy: "llm" as const,
      llm: { provider: "mistral", model: "mistral-medium-latest", temperature: 0, prompt_version: "llm-1", prompt_hash: "abcdef0123456789" },
      counts: {
        ...RUN.counts,
        llm_calls: 290,
        llm_cached: 11,
        llm_tokens_in: 210000,
        llm_tokens_out: 190000,
        llm_hallucinated_spans: 7,
        llm_claims_unanchored: 12,
      },
    };
    const api = {
      crawlRuns: vi.fn().mockResolvedValue([RUN, llmRun]),
      crawlerSettings: vi.fn(),
      crawlRun: vi.fn().mockResolvedValue({ ...llmRun, asset_counts: { by_status: {}, by_class: {} }, assets: [] }),
    };
    renderPage(api);

    const badge = await screen.findByTitle("mistral mistral-medium-latest");
    expect(badge).toHaveTextContent("LLM");
    expect(screen.getAllByRole("row")).toHaveLength(3); // header and two runs
    fireEvent.change(screen.getByLabelText("Filter by strategy"), { target: { value: "deterministic" } });
    expect(screen.getAllByRole("row")).toHaveLength(2);
    fireEvent.change(screen.getByLabelText("Filter by strategy"), { target: { value: "llm" } });
    expect(screen.getAllByRole("row")).toHaveLength(2);

    fireEvent.click(screen.getByTitle("mistral mistral-medium-latest"));
    expect(await screen.findByText(/301 documents read \(11 from cache\)/)).toHaveTextContent(
      "7 quotes not in the documents (dropped)",
    );
    expect(screen.getByText(/each document read by an AI model/)).toHaveTextContent(
      "mistral mistral-medium-latest, prompt llm-1",
    );
  });

  it("adds the LLM measures to the Scores tab when an LLM crawl has been scored", async () => {
    const llm = {
      provider: "mistral",
      model: "mistral-medium-latest",
      prompt_version: "llm-1",
      documents: 301,
      cached: 0,
      failed: 0,
      tokens_in: 214765,
      tokens_out: 183564,
      cost_usd: null,
      ms_per_document: 2864,
      hallucinated_spans: 231,
      hallucinated_span_rate: 0.0454,
    };
    const scored = { ...EVALUATION, evaluation_id: "eval-llm", crawl_run_id: "crawl_llm", strategy: "llm", metrics: { ...EVALUATION.metrics, run: { llm } } };
    const api = { crawlerEvaluations: vi.fn().mockResolvedValue([EVALUATION, scored]), crawlRuns: vi.fn(), crawlerSettings: vi.fn() };
    renderPage(api, "scores");

    expect(await screen.findByRole("columnheader", { name: "Not in doc." })).toBeInTheDocument();
    const rows = screen.getAllByRole("row");
    expect(rows).toHaveLength(3);
    const rulesCells = within(rows[1]).getAllByRole("cell").slice(-4).map((cell) => cell.textContent);
    expect(rulesCells).toEqual(["—", "—", "—", "—"]);
    const llmCells = within(rows[2]).getAllByRole("cell").slice(-4).map((cell) => cell.textContent);
    expect(llmCells).toEqual(["4.5%", "398,329", "2.9", "—"]);

    fireEvent.click(rows[2]);
    expect(await screen.findByText(/231 quotes not\s+in the documents were dropped/)).toBeInTheDocument();
    expect(screen.getByText(/links only on exact keys the model quoted/)).toBeInTheDocument();
  });

  it("shows no LLM columns when only rules crawls have been scored", async () => {
    const api = { crawlerEvaluations: vi.fn().mockResolvedValue([EVALUATION]), crawlRuns: vi.fn(), crawlerSettings: vi.fn() };
    renderPage(api, "scores");
    await screen.findByRole("columnheader", { name: "Strategy" });
    expect(screen.queryByRole("columnheader", { name: "Not in doc." })).not.toBeInTheDocument();
  });

  it("warns when the crawler has no rules and loads a preset into the editor", async () => {
    const preset = { name: "retail-returns", title: "Retail returns", description: "Store returns over a warehouse." };
    const api = {
      crawlRuns: vi.fn(),
      crawlerSettings: vi.fn().mockResolvedValue({
        active_version: null,
        using_defaults: true,
        empty: true,
        content_hash: "aaaaaaaaaaaa",
        settings: { schema_version: "2", patterns: [] },
        versions: [],
      }),
      crawlerSettingsPresets: vi.fn().mockResolvedValue([preset]),
      crawlerSettingsPreset: vi.fn().mockResolvedValue({
        ...preset,
        content_hash: "bbbbbbbbbbbb",
        settings: { schema_version: "2", patterns: [{ name: "ticket_number" }] },
      }),
    };
    renderPage(api, "settings");

    expect(await screen.findByRole("alert")).toHaveTextContent("The crawler has no rules.");
    expect(screen.getByText("Store returns over a warehouse.")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Start from “Retail returns”" }));
    await waitFor(() => expect(api.crawlerSettingsPreset).toHaveBeenCalledWith("retail-returns"));
    // The preset opens in the form: its pattern is a field, not JSON.
    expect(await screen.findByDisplayValue("ticket_number")).toHaveAttribute("aria-label", "Pattern 1 name");
    fireEvent.click(screen.getByRole("radio", { name: "JSON" }));
    expect((screen.getByLabelText("Crawler settings JSON") as HTMLTextAreaElement).value).toContain(
      "ticket_number",
    );
  });

  it("shows no warning once a version with rules is active", async () => {
    const api = {
      crawlRuns: vi.fn(),
      crawlerSettings: vi.fn().mockResolvedValue({
        active_version: 1,
        using_defaults: false,
        empty: false,
        content_hash: "cccccccccccc",
        settings: { schema_version: "2" },
        versions: [],
      }),
      crawlerSettingsPresets: vi.fn().mockResolvedValue([]),
    };
    renderPage(api, "settings");
    expect(await screen.findByText("version 1")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows what a crawl could not explain", async () => {
    const counts = {
      ...RUN.counts,
      segments: 1003,
      segments_without_mentions: 312,
      assets_without_links: 4,
      cases: 101,
      cases_unresolved: 7,
      unmatched_identifiers: 330,
      unknown_labels: 100,
      claims_dropped_weak_cue: 217,
      claims_dropped_undefined: 0,
    };
    const api = {
      crawlRuns: vi.fn().mockResolvedValue([{ ...RUN, counts }]),
      crawlerSettings: vi.fn(),
      crawlRun: vi.fn().mockResolvedValue({
        ...RUN,
        counts,
        asset_counts: { by_status: {}, by_class: {} },
        assets: [],
        coverage: [
          { signal: "unmatched_identifier", value: "AAA-9999999", count: 330, example: "RMA-3056773" },
          { signal: "unknown_label", value: "Policy number", count: 100, example: "P-88" },
        ],
      }),
    };
    renderPage(api);
    fireEvent.click((await screen.findAllByText("1ca99f86-e6a0…")).find((el) => el.tagName === "TD")!);

    const block = (await screen.findByText(/Nothing recognised in/)).closest(".crawler-coverage") as HTMLElement;
    expect(block).toHaveTextContent("Nothing recognised in 312 of 1,003 passages (31%); no linked entity in 4 documents.");
    expect(block).toHaveTextContent("7 of 101 cases were not matched to a warehouse row.");
    expect(block).toHaveTextContent("217 (a general word, and the sentence did not name the subject)");
    expect(block).not.toHaveTextContent("not defined"); // a reason with nothing dropped is not listed
    expect(within(block).getByText("RMA-3056773").closest("li")).toHaveAttribute(
      "title",
      "Shape AAA-9999999: letters as A, digits as 9",
    );
    expect(within(block).getByTitle("Seen above: P-88")).toHaveTextContent("Policy number ×100");
  });

  it("says when an older run has no coverage recorded", async () => {
    const api = {
      crawlRuns: vi.fn().mockResolvedValue([RUN]),
      crawlerSettings: vi.fn(),
      crawlRun: vi.fn().mockResolvedValue({ ...RUN, asset_counts: { by_status: {}, by_class: {} }, assets: [] }),
    };
    renderPage(api);
    fireEvent.click((await screen.findAllByText("1ca99f86-e6a0…")).find((el) => el.tagName === "TD")!);
    expect(await screen.findByText("Not recorded for this run (older crawler).")).toBeInTheDocument();
  });
});
