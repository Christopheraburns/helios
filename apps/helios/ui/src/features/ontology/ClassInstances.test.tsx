import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ClassInstances, { HighlightedText } from "./ClassInstances";

const ENTITY = {
  entity_id: "e1",
  class: "Customer",
  name: "Aaron Todd (AAAAAAAAHBOGAAAA)",
  keys: ["tpcds.customer:c_customer_sk=1"],
  source: "ds",
  crawl_run_id: "crawl_1",
  documents: 3,
};

describe("ClassInstances", () => {
  it("goes from a class to an entity's documents and the passages that mention it", async () => {
    const crawlEntities = vi.fn().mockResolvedValue({ total: 116, entities: [ENTITY] });
    const crawlEntity = vi.fn().mockResolvedValue({
      entity: ENTITY,
      documents_total: 1,
      documents: [
        {
          asset_id: "a1",
          name: "a1.eml",
          class: "Message",
          mime_type: "message/rfc822",
          timestamp: "1999-05-04T09:08:31Z",
          relationship: "Mentions",
          passages: [
            {
              segment_id: "s1",
              segment_type: "email_body",
              locator: { part: "body" },
              text: "Regards, Aaron Todd",
              mentions: [{ surface_form: "Aaron Todd", start: 9, end: 19, resolved_by: "joint", link_type: "SameAs" }],
            },
          ],
        },
      ],
      related: [{ relationship: "PartyTo", direction: "in", entity_id: "r1", class: "Return", name: "RMA-1" }],
      claims_total: 1,
      claims: [
        { predicate: "REFUND_REQUESTED", subject: null, object: null, object_value: null, evidence_count: 1, evidence: [{ asset_id: "a1", excerpt: "Refund please." }] },
      ],
    });
    const onSelectClass = vi.fn();
    const api = () => ({ crawlEntities, crawlEntity });
    render(<ClassInstances api={api} classes={["Customer"]} onSelectClass={onSelectClass} />);

    expect(await screen.findByText("Showing 1 of 116, most-documented first")).toBeInTheDocument();
    expect(crawlEntities).toHaveBeenCalledWith(["Customer"], "", 50);
    expect(screen.getByText("3 documents")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: ENTITY.name }));

    await waitFor(() => expect(crawlEntity).toHaveBeenCalledWith("e1", "crawl_1"));
    expect(await screen.findByText("1 document")).toBeInTheDocument();
    expect(screen.getAllByText("Email")).toHaveLength(2); // the document, and where the passage is
    expect(screen.getByText("1999-05-04")).toBeInTheDocument();
    const mark = screen.getByText("Aaron Todd");
    expect(mark.tagName).toBe("MARK");
    expect(mark.parentElement).toHaveTextContent("Regards, Aaron Todd");
    expect(screen.getByText("Refund please.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Return" }));
    expect(onSelectClass).toHaveBeenCalledWith("Return");

    fireEvent.click(screen.getByRole("button", { name: "← All found in documents" }));
    expect(await screen.findByLabelText("Search found entities")).toBeInTheDocument();
  });

  it("says so when the crawler found nothing for the class", async () => {
    const api = () => ({ crawlEntities: vi.fn().mockResolvedValue({ total: 0, entities: [] }), crawlEntity: vi.fn() });
    render(<ClassInstances api={api} classes={["Promotion"]} onSelectClass={vi.fn()} />);
    expect(
      await screen.findByText("The crawler has not found any of these in documents."),
    ).toBeInTheDocument();
  });

  it("highlights mentions and ignores ranges that do not fit the text", () => {
    const { container } = render(
      <HighlightedText
        text="Box for Ana and Ana again"
        mentions={[
          { start: 8, end: 11, resolved_by: "alias" },
          { start: 9, end: 12, resolved_by: "alias" },
          { start: 16, end: 19, resolved_by: "joint" },
          { start: 90, end: 99, resolved_by: "joint" },
          { start: null, end: null, resolved_by: "joint" },
        ]}
      />,
    );
    expect([...container.querySelectorAll("mark")].map((m) => m.textContent)).toEqual(["Ana", "Ana"]);
    expect(container.textContent).toBe("Box for Ana and Ana again");
  });
});
