# Entity identity in the Helios ontology

How an ontology recognises the same thing across reports and sources, written as
declared identity keys ([ontology-plan.md, Identity keys](ontology-plan.md#identity-keys)),
so resolution needs no domain code. This is the mechanism only: it names no domain.
The schemes, checks and limits for a domain belong to its pack, and a deployment's
sources and their trust to its extension.

Each rule has an example from Horus (ships and aircraft) and from a car plant, and
must work for both. Horus's packs and extension, and the evidence from its lake that
shaped these rules (shared MMSIs, crew-typed IMO numbers, renumbered provider ids),
are in its [docs/IDENTITY.md](https://github.com/Christopheraburns/horus/blob/main/docs/IDENTITY.md). Moved here from there on 2026-11-20.

Built in: S1 (identity keys), S4 (identifier rows in the entity index), S11
(resolution), in [ontology-burndown.md](ontology-burndown.md).

Nothing here names a domain. Each rule has a Horus and a car plant example, and
must work for both.

### Principles

1. **An entity's primary key is its own.** The entity index mints it (a ULID). No
   outside identifier is ever a primary key: every one can be missing, wrong,
   reused or changed, and the entity outlives all of them. *Horus:* a vessel's id
   is not its MMSI. *Car plant:* a vehicle's id is not its VIN, which is assigned
   after the body is built.
2. **Observations carry claims, not entities.** An observation says which
   identifier the source reported; it never stores an entity id. Which entity a
   claim belongs to is resolved through the identity keys at the observation's
   time, and can be resolved again when the rules or the evidence change. *Horus:*
   "MMSI 636019825 was here at 10:02". *Car plant:* "serial S-123 passed station 4
   at 10:02".
3. **Identifiers are valid for a time.** The entity index keeps one identifier row
   per entity, scheme and value, with `valid_from` and `valid_to`, so a value that
   moved between entities resolves differently before and after the move.
4. **A value is checked before it is trusted.** A scheme names a value type, and a
   value type can name a check, supplied by the pack (a format, a check digit,
   reserved ranges, known junk values). A value that fails is not an identifier.
5. **The source caps the strength.** A deployment declares, on each of its sources,
   which schemes it can vouch for (`vouchesFor`). A strong key from a source that can't vouch for it behaves as
   weak: it proposes and never links on its own.
6. **Conflicts are flagged, never resolved silently.** Two entities claiming the
   same exclusive value at the same time, or one entity with two values of a
   permanent key, become a review item. Nothing merges without a link rule or a
   decision.

### Identity key fields

The plan's four fields, plus `exclusive` and `concurrencyWindow` (accepted 2026-11-20):

| Field | Values | Meaning | Horus | Car plant |
|---|---|---|---|---|
| `scheme` | a name | The kind of identifier; a value is unique within its scheme and scope | `mmsi` | `vin` |
| `strength` | `strong`, `weak` | A strong match links on its own; a weak match only proposes | MMSI strong, name weak | VIN strong, supplier name weak |
| `scope` | `global`, or the property that qualifies it | Where a value is unique | global | a lot number per supplier |
| `validity` | `permanent`, `reassignable` | Whether a value can move to another instance over time | an MMSI is reissued | a serial never moves |
| `exclusive` | `true` (default), `false` | Whether a value names at most one instance *at any moment*. `reassignable` covers a value moving over time, not two instances using it at once | an MMSI shared by two ships | one serial printed on two parts |
| `concurrencyWindow` | a duration | How close in time two claims of an exclusive value must be to count as "at once" | set by the pack | set by the pack |

"At once" also needs a plausibility test: could one instance have made both
observations? For types that implement `Located`, the core compares the implied
speed with the type's declared `maxSpeed` (a pack value). Types without a location
use the window alone.

### Identifier rows

The entity index stores identity as rows, with no domain columns:

| Column | Meaning |
|---|---|
| `entity` | The entity's primary key |
| `scheme`, `value` | The identifier |
| `valid_from`, `valid_to` | When it applied (`valid_to` empty: current) |
| `source`, `confidence` | Who said so, and how sure |
| `status` | `active`, `contested` (claimed by more than one entity at once), or `ended` |

An entity can hold several values of one scheme at once, unless the scheme is
permanent: a source may list one thing under two of its own ids (seen with
hormuz.now: [Horus evidence 7](https://github.com/Christopheraburns/horus/blob/main/docs/IDENTITY.md#evidence-from-the-lake-2026-11-20)). Exclusivity is the other direction: one value, one entity.

### Resolution

Entity resolution (S11) reads only the declarations above:

| Evidence | Result |
|---|---|
| A strong key that passes its check, from a source that can vouch for it, held by one entity at that time | link |
| A strong key that no entity holds | a provisional entity with that key |
| A strong key from a source that can't vouch for it | a proposal |
| A weak key | a proposal |
| An exclusive key claimed by two entities within its window, implausibly for one | both identifier rows `contested`; a conflict to review; claims of that value link to neither while it lasts |
| One entity with two values of a permanent key | a conflict to review |
| A value that fails its check | not an identifier; resolve by another key on the same observation, or not at all |

A decision on a conflict (which entity really holds the value, or that one is
false) is an action, recorded with who decided and the evidence, and it ends the
contest.

### Decisions

1. `exclusive` and `concurrencyWindow` are identity key fields (S1). Accepted
   2026-11-20, from Horus's evidence of MMSIs used by two ships at once.
2. Source trust is declared on each source, in the
   extension (`vouchesFor: [scheme, ...]`), not as a policy. It is provenance (in
   RDF, PROV-O: a source is a `prov:Agent`, a claim a `prov:Entity` attributed to
   it), the same for every caller, and owned by the deployment. Yes or no per
   scheme to start; a confidence per scheme only if a real case needs it.
