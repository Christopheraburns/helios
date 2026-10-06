import { afterEach, describe, expect, it, vi } from "vitest";

import { HeliosApiClient } from "./client";

afterEach(() => vi.unstubAllGlobals());

function respond(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }),
    ),
  );
}

describe("API error messages", () => {
  it("names the fields the API rejected instead of only the status code", async () => {
    respond(422, {
      detail: [
        { type: "string_too_short", loc: ["body", "connection_ref"], msg: "String should have at least 1 character" },
        { type: "missing", loc: ["body", "name"], msg: "Field required" },
        { type: "int_parsing", loc: ["body", "scope", "max_bytes"], msg: "Input should be a valid integer" },
      ],
    });
    await expect(new HeliosApiClient("https://api.example").crawlRuns()).rejects.toThrow(
      "The request was not accepted.\n• connection ref: is required\n• name: is required\n• scope.max bytes: Input should be a valid integer",
    );
  });

  it("still falls back to the status code when the body explains nothing", async () => {
    respond(422, {});
    await expect(new HeliosApiClient("https://api.example").crawlRuns()).rejects.toThrow(
      "The Helios API returned HTTP 422.",
    );
  });
});
