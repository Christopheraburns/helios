import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it } from "vitest";

import { docEntry, docMarkdown } from "../../docs/catalog";
import MarkdownView from "./MarkdownView";

it("the crawler guide is bundled and its in-page links have targets", () => {
  const entry = docEntry("helios", "crawler-guide");
  expect(entry).toBeDefined();
  const { container } = render(
    <MemoryRouter>
      <MarkdownView markdown={docMarkdown(entry!)} />
    </MemoryRouter>,
  );
  const ids = new Set(Array.from(container.querySelectorAll("[id]")).map((e) => e.id));
  const targets = Array.from(container.querySelectorAll('a[href^="#"]')).map((a) =>
    a.getAttribute("href")!.slice(1),
  );
  expect(targets.length).toBeGreaterThan(0);
  expect(targets.filter((target) => !ids.has(target))).toEqual([]);
});
