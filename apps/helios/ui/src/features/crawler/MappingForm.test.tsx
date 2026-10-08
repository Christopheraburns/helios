import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import type { MappingModel, MappingProbe } from "../../api/client";
import MappingForm, { type Mapping, type ProbeTarget } from "./MappingForm";
import MappingTab, { ProbePanel } from "./MappingTab";

const MODEL: MappingModel = {
  model: "clinic.ossie.yaml",
  name: "clinic",
  database: "clinic",
  tables: [
    {
      name: "patients",
      source: "clinic.patients",
      label: "Patients",
      description: "",
      columns: [
        { name: "patient_id", label: "Patient key", datatype: "Integer", role: "identifier" },
        { name: "mrn", label: "Record number", datatype: "String", role: "attribute" },
        { name: "full_name", label: "Name", datatype: "String", role: "attribute" },
      ],
    },
    {
      name: "visits",
      source: "clinic.visits",
      label: "Visits",
      description: "",
      columns: [
        { name: "visit_id", label: "", datatype: "Integer", role: "identifier" },
        { name: "visit_code", label: "", datatype: "String", role: "" },
        { name: "patient_id", label: "", datatype: "Integer", role: "" },
        { name: "visit_date", label: "", datatype: "Date", role: "" },
      ],
    },
  ],
  relationships: [{ name: "visits__patient_id__patients", from: "visits", to: "patients", from_columns: ["patient_id"], to_columns: ["patient_id"] }],
};

const MAPPING: Mapping = {
  model: "clinic.ossie.yaml",
  ontology_version: "clinic@1.0.0",
  database: "clinic",
  entities: [
    { ossie_element: "patients", class: "Patient", identifiers: { primary: ["patient_id"], secondary: ["mrn"], display: ["full_name"], aliases: [], alias_templates: [] }, attributes: [{ attribute: "name", columns: ["full_name"] }] },
    { ossie_element: "visits", class: "Visit", identifiers: { primary: ["visit_id"], secondary: ["visit_code"], display: [], aliases: [], alias_templates: [] } },
  ],
  relationships: [],
  concepts: [],
  anchors: [
    {
      class: "Visit",
      lookups: [{ column: "visit_code", match: "equals", type: "string", source_columns: [], identifies: "Visit" }],
      joins: [{ column: "patient_id", class: "Patient", edge: "AttendedBy" }],
      colocated: [],
      date: { column: "visit_date" },
      related: [],
      row_limit: 5,
      min_constraint_kinds: 2,
    },
  ],
};
const CLASSES = ["AttendedBy", "Clinician", "Patient", "Visit"];

function Harness({ seen, onProbe = vi.fn(), start = MAPPING }: { seen: { current: Mapping }; onProbe?: (target: ProbeTarget, title: string) => void; start?: Mapping }) {
  const [mapping, setMapping] = useState(start);
  seen.current = mapping;
  return <MappingForm mapping={mapping} model={MODEL} models={[MODEL.model]} classes={CLASSES} onChange={setMapping} onProbe={onProbe} />;
}

describe("MappingForm", () => {
  it("maps a class to a table with columns picked from the semantic model", () => {
    const seen = { current: MAPPING };
    const onProbe = vi.fn();
    render(<Harness seen={seen} onProbe={onProbe} />);

    expect(screen.getByLabelText("Entry 1 table")).toHaveValue("patients");
    const add = screen.getByLabelText("Add to Patient other identifiers");
    // The table's columns are offered.
    expect(document.getElementById(add.getAttribute("list") ?? "")?.querySelectorAll("option")).toHaveLength(3);
    fireEvent.change(add, { target: { value: "full_name" } });
    fireEvent.keyDown(add, { key: "Enter" });
    expect(seen.current.entities[0].identifiers.secondary).toEqual(["mrn", "full_name"]);
    // What the form does not show is kept.
    expect(seen.current.entities[0].attributes).toEqual(MAPPING.entities[0].attributes);
    expect(seen.current.anchors).toBe(MAPPING.anchors);

    fireEvent.click(screen.getAllByRole("button", { name: "Check in the warehouse" })[0]);
    expect(onProbe).toHaveBeenCalledWith({ entity: "Patient" }, "Patient in the warehouse");

    fireEvent.click(screen.getByRole("button", { name: "Add a class" }));
    expect(seen.current.entities).toHaveLength(3);
    fireEvent.change(screen.getByLabelText("Entry 3 table"), { target: { value: "appointments" } });
    expect(screen.getByText(/“appointments” is not in the semantic model/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Entry 3 table"), { target: { value: "visits" } });
    expect(screen.getByText(/Marked as identifiers when the table was profiled/)).toHaveTextContent("visit_id");
    fireEvent.click(screen.getByRole("button", { name: "Remove entry 3" }));
    expect(seen.current.entities.map((e: Mapping) => e.class)).toEqual(["Patient", "Visit"]);
  });

  it("edits a case record: identifiers, joined classes, date and related records", () => {
    const seen = { current: MAPPING };
    const onProbe = vi.fn();
    render(<Harness seen={seen} onProbe={onProbe} />);
    fireEvent.click(screen.getByRole("tab", { name: "Case records" }));

    fireEvent.change(screen.getByLabelText("Visit identifier 1 match"), { target: { value: "ends_with" } });
    expect(seen.current.anchors[0].lookups[0].match).toBe("ends_with");
    fireEvent.change(screen.getByLabelText("Visit join 1 relationship"), { target: { value: "SeenBy" } });
    expect(seen.current.anchors[0].joins[0]).toEqual({ column: "patient_id", class: "Patient", edge: "SeenBy" });

    // A date in a table of dates needs that table's key and value; clearing the table drops them.
    fireEvent.change(screen.getByLabelText("Visit date table"), { target: { value: "patients" } });
    fireEvent.change(screen.getByLabelText("Visit date table key"), { target: { value: "patient_id" } });
    expect(seen.current.anchors[0].date).toEqual({ column: "visit_date", table: "patients", key: "patient_id" });
    fireEvent.change(screen.getByLabelText("Visit date table"), { target: { value: "" } });
    expect(seen.current.anchors[0].date).toEqual({ column: "visit_date" });
    fireEvent.change(screen.getByLabelText("Visit date column"), { target: { value: "" } });
    expect(seen.current.anchors[0].date).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Add a related record" }));
    const joinOn = screen.getByLabelText("Add to Visit related 1 join columns");
    fireEvent.change(joinOn, { target: { value: "patient_id = patient_id" } });
    fireEvent.keyDown(joinOn, { key: "Enter" });
    expect(seen.current.anchors[0].related[0].join_on).toEqual([["patient_id", "patient_id"]]);

    fireEvent.click(screen.getByRole("button", { name: "Run its query" }));
    expect(onProbe).toHaveBeenCalledWith({ anchor: "Visit" }, "The Visit query");
    expect(seen.current.entities).toBe(MAPPING.entities);
  });

  it("edits thresholds and says what it leaves to the JSON view", () => {
    const seen = { current: MAPPING };
    render(<Harness seen={seen} />);
    fireEvent.click(screen.getByRole("tab", { name: "Relationships and thresholds" }));
    fireEvent.change(screen.getByLabelText("A near spelling"), { target: { value: "0.9" } });
    expect(seen.current.resolution.thresholds.fuzzy_min_score).toBe(0.9);
    expect(screen.getByText(/also has attribute bindings/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add a relationship" }));
    fireEvent.change(screen.getByLabelText("Relationship 1 type"), { target: { value: "AttendedBy" } });
    expect(seen.current.relationships).toEqual([{ ossie_relationship: "", edge: "AttendedBy" }]);
  });
});

describe("ProbePanel", () => {
  it("shows a key that is not unique, the sample and the SQL", () => {
    const probe: MappingProbe = {
      kind: "entity",
      class: "Clinician",
      table: "clinic.clinicians",
      rows: 3,
      distinct_keys: 2,
      key_is_unique: false,
      columns: ["clinician_id", "full_name"],
      sample: [[7, "Amara Lee"], [8, null]],
      findings: ["The key is not unique: 3 rows but 2 different keys."],
      sql: ["SELECT COUNT(*) FROM clinic.clinicians"],
    };
    render(<ProbePanel title="Clinician in the warehouse" probe={probe} problem={null} busy={false} />);
    expect(screen.getByRole("alert")).toHaveTextContent("The key is not unique");
    expect(screen.getByText("Amara Lee")).toBeInTheDocument();
    expect(screen.getByText("null")).toBeInTheDocument();
    expect(screen.getByText("SELECT COUNT(*) FROM clinic.clinicians")).toBeInTheDocument();
    expect(screen.queryByText("The key is unique.")).not.toBeInTheDocument();
  });
});

describe("MappingTab", () => {
  const version = { version: 2, model: "clinic.ossie.yaml", ontology_version: "clinic@1.0.0", content_hash: "abc", created_at: "2026-10-07T10:00:00Z", created_by: "alice", note: "first", is_active: true };

  function api(overrides: Record<string, unknown> = {}) {
    return {
      mappings: vi.fn().mockResolvedValue({ active: { "clinic.ossie.yaml": 2 }, versions: [version] }),
      shippedMappings: vi.fn().mockResolvedValue([{ ...MAPPING, model: "tpcds.ossie.yaml" }]),
      mappingVersion: vi.fn().mockResolvedValue({ ...version, mapping: MAPPING }),
      mappingModels: vi.fn().mockResolvedValue({ models: ["clinic.ossie.yaml"], classes: CLASSES }),
      mappingModel: vi.fn().mockResolvedValue(MODEL),
      validateMapping: vi.fn().mockResolvedValue({ valid: false, problems: ["entities[0] (Patient): column 'nhs' is not in table 'patients'"], not_checked: [] }),
      saveMapping: vi.fn().mockResolvedValue({ ...version, version: 3, is_active: false, created: true, not_checked: [] }),
      activateMapping: vi.fn().mockResolvedValue({ version: 3, model: "clinic.ossie.yaml" }),
      probeMapping: vi.fn().mockResolvedValue({ kind: "entity", class: "Patient", table: "clinic.patients", rows: 3, distinct_keys: 3, key_is_unique: true, columns: ["patient_id"], sample: [[10]], findings: [], sql: ["SELECT 1"] }),
      ...overrides,
    };
  }

  it("opens the active version in the form, checks it, saves and activates", async () => {
    const client = api();
    render(<MappingTab api={() => client} />);

    expect(await screen.findByLabelText("Entry 1 table")).toHaveValue("patients");
    expect(screen.getByText(/loaded: version 2 \(active\)/)).toBeInTheDocument();
    await waitFor(() => expect(client.mappingModel).toHaveBeenCalledWith("clinic.ossie.yaml"));

    fireEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByText(/column 'nhs' is not in table 'patients'/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Entry 1 table"), { target: { value: "people" } });
    fireEvent.change(screen.getByPlaceholderText("Note: what changed and why"), { target: { value: "moved" } });
    fireEvent.click(screen.getByRole("button", { name: "Check and save" }));
    await waitFor(() => expect(client.saveMapping).toHaveBeenCalled());
    const [savedMapping, note] = client.saveMapping.mock.calls[0];
    expect(savedMapping.entities[0].ossie_element).toBe("people");
    expect(note).toBe("moved");

    fireEvent.click(await screen.findByRole("button", { name: "Activate version 3" }));
    await waitFor(() => expect(client.activateMapping).toHaveBeenCalledWith(3));
  });

  it("asks the warehouse about the mapping as it is being edited", async () => {
    const client = api();
    render(<MappingTab api={() => client} />);
    fireEvent.click((await screen.findAllByRole("button", { name: "Check in the warehouse" }))[0]);
    const panel = await screen.findByRole("complementary", { name: "Warehouse check" });
    expect(await within(panel).findByText("The key is unique.", { exact: false })).toBeInTheDocument();
    expect(client.probeMapping.mock.calls[0][1]).toEqual({ entity: "Patient" });
  });

  it("shows the warehouse's refusal, and a save that is refused", async () => {
    const client = api({
      probeMapping: vi.fn().mockRejectedValue(new Error("the warehouse refused the query: no such column nhs")),
      saveMapping: vi.fn().mockRejectedValue(new Error("invalid mapping: needs at least one primary key column")),
    });
    render(<MappingTab api={() => client} />);
    fireEvent.click((await screen.findAllByRole("button", { name: "Check in the warehouse" }))[0]);
    expect(await screen.findByText(/no such column nhs/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Check and save" }));
    expect(await screen.findByText(/needs at least one primary key column/)).toBeInTheDocument();
  });

  it("starts empty when nothing is saved and offers the examples", async () => {
    const client = api({ mappings: vi.fn().mockResolvedValue({ active: {}, versions: [] }) });
    render(<MappingTab api={() => client} />);
    expect(await screen.findByText(/No mapping is active/)).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Start from the example for tpcds.ossie.yaml" }));
    expect(await screen.findByLabelText("Entry 1 table")).toHaveValue("patients");
    expect(client.mappingVersion).not.toHaveBeenCalled();
  });
});
