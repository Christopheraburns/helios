import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EntityClaimsResult, EvidenceSearchResult, whereInDocument } from "./EvidenceResults";

describe("document results in a conversation", () => {
  it("shows each passage with where it is, how well it matched and what it is linked to", () => {
    render(
      <EvidenceSearchResult
        result={{
          query: "damaged packaging",
          count: 1,
          segments: [
            {
              segment_id: "s1",
              asset_id: "a1",
              segment_type: "email_body",
              locator: { part: "body", start: 0, end: 24 },
              text: "The box arrived crushed.",
              relevance: 0.8231,
              entities: [
                { entity_id: "e1", class: "Customer", name: "Angela Raymond", keys: ["tpcds.customer:c_customer_sk=54201"] },
              ],
            },
          ],
        }}
      />,
    );
    expect(screen.getByText("The box arrived crushed.")).toBeInTheDocument();
    expect(screen.getByText("Email")).toBeInTheDocument();
    expect(screen.getByText("82% match")).toBeInTheDocument();
    expect(screen.getByText("Angela Raymond", { exact: false })).toHaveAttribute(
      "title",
      "tpcds.customer:c_customer_sk=54201",
    );
  });

  it("says so when nothing matched", () => {
    render(<EvidenceSearchResult result={{ query: "unicorns", count: 0, segments: [] }} />);
    expect(screen.getByText("No passage in the crawled documents matched.")).toBeInTheDocument();
  });

  it("shows claims with their supporting passages", () => {
    render(
      <EntityClaimsResult
        result={{
          entity: "angela",
          entities: [{ entity_id: "e1", class: "Customer", name: "Angela Raymond", source: "ds" }],
          claims_total: 3,
          claims: [
            {
              predicate: "REFUND_REQUESTED",
              subject: { name: "Return 166147", class: "Return" },
              object: { name: "Angela Raymond", class: "Customer" },
              object_value: null,
              confidence: 0.9,
              evidence_count: 2,
              evidence: [{ asset_id: "a1", locator: { page: 2, text: "x" }, excerpt: "When will the refund post?" }],
            },
          ],
        }}
      />,
    );
    expect(screen.getByText("What documents say about Customer Angela Raymond")).toBeInTheDocument();
    expect(screen.getByText("1 of 3 claims")).toBeInTheDocument();
    expect(screen.getByText("refund requested")).toBeInTheDocument();
    expect(screen.getByText("When will the refund post?", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("PDF, page 2")).toBeInTheDocument();
  });

  it("names where a passage is", () => {
    expect(whereInDocument({ message_id: "m1" }, "message")).toBe("Chat message");
    expect(whereInDocument({ header: "From" })).toBe("Email From line");
  });
});
