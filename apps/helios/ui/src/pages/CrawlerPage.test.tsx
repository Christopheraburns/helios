import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    fireEvent.click(button);

    await waitFor(() => expect(startCrawl).toHaveBeenCalledWith(target, false, "after the pattern fix"));
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
    expect(await screen.findByText("the built-in defaults")).toBeTruthy();
    fireEvent.click(screen.getByText("Validate and save"));
    expect(await screen.findByText(/not a valid regular expression/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Crawler settings JSON"), { target: { value: "{ not json" } });
    fireEvent.click(screen.getByText("Validate and save"));
    expect(await screen.findByText(/Not valid JSON/)).toBeTruthy();
  });
});
