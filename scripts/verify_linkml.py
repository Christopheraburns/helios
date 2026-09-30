"""Check that SchemaView can read the Helios ontology in this runtime.

Run inside a Workbench Session on the helios runtime:

    python scripts/verify_linkml.py

This checks what the O-2 parser relies on, not merely that the import works:
relative imports across the core -> pack -> extension chain, inherited slots
with resolved ranges, and enums. If this passes, the parser has solid ground.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAYERS = {
    "core": ROOT / "ontology/core/core.yaml",
    "pack": ROOT / "ontology/packs/retail/retail.yaml",
    "extension": ROOT / "ontology/customers/example-tenant/extension.yaml",
}

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{f' -- {detail}' if detail else ''}")
    if not condition:
        failures.append(label)


print("=" * 70)
try:
    import linkml_runtime
    from linkml_runtime.utils.schemaview import SchemaView
except ImportError as exc:
    print(f"linkml_runtime is not installed in this runtime: {exc}")
    print("\nThis needs helios runtime >= 0.1.3. Check the session's runtime with:")
    print("  env | grep ML_RUNTIME_FULL_VERSION")
    raise SystemExit(1)

print(f"linkml_runtime {linkml_runtime.__version__} | python {sys.version.split()[0]}")
print("=" * 70)

# 1. Each layer loads on its own.
views: dict[str, SchemaView] = {}
for name, path in LAYERS.items():
    print(f"\n[{name}] {path.relative_to(ROOT)}")
    try:
        view = SchemaView(str(path))
        classes = view.all_classes()
        views[name] = view
        check("loads", True, f"{len(classes)} classes visible, schema {view.schema.name!r}")
    except Exception as exc:
        check("loads", False, f"{type(exc).__name__}: {exc}")

if "extension" not in views:
    print("\nThe extension layer did not load; stopping.")
    raise SystemExit(1)

# 2. Relative imports resolve, so the deepest layer sees all three.
deep = views["extension"]
names = set(deep.all_classes())
print("\n[imports] core -> pack -> extension resolved through relative paths")
check("core class visible from extension", "Thing" in names, "Thing")
check("pack class visible from extension", "Customer" in names, "Customer")
check("extension's own class present", "FranchisePartner" in names, "FranchisePartner")

# 3. is_a ancestry crosses layer boundaries.
print("\n[inheritance] is_a chains")
try:
    ancestors = deep.class_ancestors("FranchisePartner")
    check(
        "FranchisePartner reaches core Thing",
        {"Organization", "EnterpriseEntity", "Thing"} <= set(ancestors),
        " -> ".join(ancestors),
    )
except Exception as exc:
    check("FranchisePartner ancestry", False, f"{type(exc).__name__}: {exc}")

# 4. Induced slots: the parser builds HAS_ATTRIBUTE edges from these.
print("\n[slots] induced slots carry inherited attributes with resolved ranges")
try:
    induced = {slot.name: slot for slot in deep.class_induced_slots("Customer")}
    check("inherited from Thing", "entity_id" in induced, "entity_id")
    check("inherited from Person", "affiliated_with" in induced, "affiliated_with")
    check("declared on Customer", "loyalty_tier" in induced, "loyalty_tier")
    affiliated = induced.get("affiliated_with")
    if affiliated is not None:
        check(
            "range resolves to a class",
            affiliated.range == "Organization",
            f"affiliated_with -> {affiliated.range}, multivalued={affiliated.multivalued}",
        )
    identifier = [name for name, slot in induced.items() if slot.identifier]
    check("identifier slot detected", identifier == ["entity_id"], str(identifier))
    print(f"        Customer has {len(induced)} induced slots")
except Exception as exc:
    check("induced slots", False, f"{type(exc).__name__}: {exc}")

# 5. Enums, which become Enum/EnumValue nodes.
print("\n[enums]")
try:
    enums = deep.all_enums()
    check("enums resolve", "ResolutionTier" in enums, ", ".join(sorted(enums)))
    tier = enums["ResolutionTier"].permissible_values
    check(
        "permissible values readable",
        "exact_key" in tier,
        f"ResolutionTier: {', '.join(sorted(tier))}",
    )
except Exception as exc:
    check("enums", False, f"{type(exc).__name__}: {exc}")

# 6. Rough scale, against the burndown's "~45 classes and ~100 attributes".
print("\n[scale]")
total_slots = 0
for class_name in deep.all_classes():
    try:
        total_slots += len(deep.class_induced_slots(class_name))
    except Exception:
        pass
print(f"        {len(deep.all_classes())} classes, {total_slots} induced slots, "
      f"{len(deep.all_enums())} enums (extension layer)")

print("\n" + "=" * 70)
if failures:
    print(f"{len(failures)} check(s) FAILED: {', '.join(failures)}")
    raise SystemExit(1)
print("All checks passed. SchemaView is usable for the O-2 parser.")
