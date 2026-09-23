# Model graph API

`GET /api/v1/models/{model_id}/graph` returns an authorized, schema-agnostic
projection of one Helios Model. The contract is independent of React Flow or any
other rendering library.

```json
{
  "model_id": "customer360",
  "organization_id": "acme",
  "nodes": [
    {
      "id": "metric:lifetime_value",
      "kind": "metric",
      "label": "Lifetime value",
      "status": "published",
      "confidence": 0.9,
      "evidence": "Derived from accepted semantic definitions",
      "metadata": {"description": "Value over the customer relationship"},
      "permitted_actions": ["semantic.read"]
    }
  ],
  "edges": [
    {
      "id": "relationship:customer:value",
      "kind": "semantic_relationship",
      "source": "concept:customer",
      "target": "metric:lifetime_value",
      "status": "published",
      "confidence": null,
      "evidence": null,
      "metadata": {},
      "permitted_actions": ["semantic.read"]
    }
  ],
  "summary": {
    "node_count": 1,
    "edge_count": 0,
    "node_kinds": ["metric"],
    "edge_kinds": []
  }
}
```

## Contract

Node and edge `kind` values are open vocabularies. Initial values include
`domain`, `concept`, `data_source`, `dataset`, `table`, `view`, `attribute`,
`column`, `metric`, `physical_relationship`, `inferred_relationship`,
`semantic_relationship`, and `ontology_relationship`. Frontends must tolerate
unknown kinds and metadata fields.

IDs are stable within a Model. `label` is display text and must not be used as an
identity. `status`, `confidence`, and `evidence` may be null or omitted by future
producers when unavailable. `metadata` contains kind-specific, non-secret values.
`permitted_actions` is advisory UI metadata calculated by backend policy; API
operations remain independently authorized.

## Authorization and non-disclosure

The server loads only the requested Model's graph, verifies its organization and
Model identity, and projects it through Helios authorization policy. Draft or
proposed content requires edit access. Physical dataset/attribute content requires
DataSource read access. Semantic and ontology content require their corresponding
read actions.

Filtering occurs before serialization:

- cross-organization or cross-Model objects are discarded;
- edges are returned only when the edge and both endpoints are visible;
- summary counts and kind lists are computed from visible objects only;
- hidden IDs, labels, evidence, metadata, and edge references are not returned.

The frontend must never be responsible for hiding unauthorized graph elements.
