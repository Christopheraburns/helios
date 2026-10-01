import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ApiUnavailableError, type HeliosApi } from "../api/client";
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
