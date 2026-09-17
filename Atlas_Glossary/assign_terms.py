"""
assign_terms.py
1. Imports a glossary CSV into Atlas (bulk import API).
2. Assigns each term to the hive/iceberg column entities listed in tpcds_term_columns.csv.

Usage:
  export ATLAS_BASE="https://<datalake-host>/<datalake-name>/cdp-proxy-api/atlas/api/atlas/v2"
  export ATLAS_USER=<workload-username> ATLAS_PASS=<workload-password>
  python assign_terms.py tpcds_glossary_full.csv          # or tpcds_glossary_seed.csv
Optional: DB=tpcds  COLUMN_TYPES="hive_column,iceberg_column"
"""
import csv, os, sys, requests

BASE  = os.environ["ATLAS_BASE"].rstrip("/")
AUTH  = (os.environ["ATLAS_USER"], os.environ["ATLAS_PASS"])
DB    = os.environ.get("DB", "tpcds")
TYPES = os.environ.get("COLUMN_TYPES", "hive_column,iceberg_column").split(",")
csv_path = sys.argv[1] if len(sys.argv) > 1 else "tpcds_glossary_full.csv"
HERE = os.path.dirname(os.path.abspath(__file__))
S = requests.Session(); S.auth = AUTH; S.verify = True

# --- 1. import glossary
with open(csv_path, "rb") as f:
    r = S.post(f"{BASE}/glossary/import", files={"file": (os.path.basename(csv_path), f, "text/csv")})
print("import:", r.status_code, r.text[:300])
r.raise_for_status()

# --- 2. resolve glossary + term guids
with open(csv_path) as f:
    gname = next(csv.DictReader(f))["GlossaryName"]
gl = next(g for g in S.get(f"{BASE}/glossary").json() if g["name"] == gname)
terms = S.get(f"{BASE}/glossary/{gl['guid']}/terms", params={"limit": 1000}).json()
guid_of = {t["name"]: t["guid"] for t in terms}
print(f"glossary '{gname}': {len(guid_of)} terms")

# --- 3. find column entities and assign
def find_column(table, column):
    for typ in TYPES:
        q = f'{typ} where qualifiedName like "{DB}.{table}.{column}@*"'
        r = S.get(f"{BASE}/search/dsl", params={"query": q, "limit": 5})
        if r.status_code != 200:
            continue
        ents = r.json().get("entities") or []
        ents = [e for e in ents if e.get("status", "ACTIVE") == "ACTIVE"]
        if ents:
            return ents[0]["guid"], typ
    return None, None

terms_in_file = set(guid_of)
by_term = {}
with open(os.path.join(HERE, "tpcds_term_columns.csv")) as f:
    for row in csv.DictReader(f):
        if row["term"] in terms_in_file:
            by_term.setdefault(row["term"], []).append((row["table"], row["column"]))

missing, done = [], 0
for term, cols in by_term.items():
    payload = []
    for tb, col in cols:
        guid, typ = find_column(tb, col)
        if guid: payload.append({"guid": guid, "typeName": typ})
        else:    missing.append(f"{tb}.{col}")
    if payload:
        r = S.post(f"{BASE}/glossary/terms/{guid_of[term]}/assignedEntities", json=payload)
        if r.status_code in (200, 204): done += len(payload)
        else: print(f"assign failed for '{term}': {r.status_code} {r.text[:200]}")
print(f"assigned {done} columns; {len(missing)} columns not found in Atlas")
for m in missing[:20]: print("  missing:", m)
