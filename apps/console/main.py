"""helios console: FastAPI application. Server-rendered pages with HTMX for in-place updates."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from helios_core import __version__
from helios_core.atlas import AtlasClient, AtlasError
from helios_core.config import atlas_config, impala_config, inference_config
from helios_core.engines import ImpalaEngine
from helios_core import runs as runstore
from fastapi import HTTPException

HERE = Path(__file__).parent
app = FastAPI(title="helios console")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
tpl = Jinja2Templates(directory=HERE / "templates")


def atlas() -> AtlasClient:
    cfg = atlas_config()
    if cfg is None:
        raise AtlasError(0, "ATLAS_BASE / ATLAS_USER / ATLAS_PASS are not set in the project environment")
    return AtlasClient(cfg)


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    ctx.setdefault("version", __version__)
    ctx.setdefault("error", None)
    return tpl.TemplateResponse(request, name, ctx)


@app.exception_handler(AtlasError)
async def atlas_error(request: Request, exc: AtlasError):
    return render(request, "error.html", message=str(exc), back=request.headers.get("referer", "/"))


# ---------------------------------------------------------------- health
def _check(fn):
    try:
        fn()
        return "ok", ""
    except Exception as e:  # noqa: BLE001
        return "failed", str(e)[:300]


@app.get("/", response_class=HTMLResponse)
def health(request: Request):
    checks = []
    a = atlas_config()
    checks.append(("Atlas", a.base_url if a else "not configured", *(_check(lambda: AtlasClient(a).ping()) if a else ("skipped", "set ATLAS_BASE, ATLAS_USER, ATLAS_PASS"))))
    i = impala_config()
    checks.append(("Impala", f"{i.host}:{i.port}" if i else "not configured", *(_check(lambda: ImpalaEngine(i).ping()) if i else ("skipped", "set IMPALA_HOST (and IMPALA_USER / IMPALA_PASS if different from Atlas)"))))
    inf = inference_config()
    if inf.base_url:
        import httpx
        checks.append(("AI Inference", inf.base_url, *_check(lambda: httpx.get(f"{inf.base_url.rstrip('/')}/models",
                      headers={"Authorization": f"Bearer {inf.api_key}"} if inf.api_key else {}, timeout=15).raise_for_status())))
    else:
        checks.append(("AI Inference", "not configured", "skipped", "set INFERENCE_BASE_URL, INFERENCE_API_KEY, INFERENCE_MODEL"))
    return render(request, "health.html", checks=checks, active="health")


# ---------------------------------------------------------------- glossaries
@app.get("/glossary", response_class=HTMLResponse)
def glossaries(request: Request):
    return render(request, "glossaries.html", glossaries=atlas().list_glossaries(), active="glossary")


@app.post("/glossary")
def create_glossary(name: str = Form(...), short_description: str = Form("")):
    g = atlas().create_glossary(name.strip(), short_description.strip())
    return RedirectResponse(f"/glossary/{g['guid']}", status_code=303)


@app.post("/glossary/{guid}/delete")
def delete_glossary(guid: str):
    atlas().delete_glossary(guid)
    return RedirectResponse("/glossary", status_code=303)


@app.get("/glossary/{guid}", response_class=HTMLResponse)
def terms(request: Request, guid: str, q: str = ""):
    a = atlas()
    g = a.get_glossary(guid)
    ts = sorted(a.list_terms(guid), key=lambda t: t["name"].lower())
    if q:
        ql = q.lower()
        ts = [t for t in ts if ql in t["name"].lower() or ql in (t.get("shortDescription") or "").lower()]
    return render(request, "terms.html", glossary=g, terms=ts, q=q, active="glossary")


@app.post("/glossary/{guid}/import")
async def import_terms(request: Request, guid: str, file: UploadFile):
    a = atlas()
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        f.write(await file.read())
        path = f.name
    try:
        result = a.import_csv(path)
        n = len(result.get("successImportInfoList", [])) if isinstance(result, dict) else 0
        return render(request, "notice.html", message=f"Imported {n} terms.", back=f"/glossary/{guid}")
    except AtlasError as e:
        if e.status == 409:
            return render(request, "error.html", message="Atlas rejected the import because one or more terms already exist. "
                          "This Atlas does not update terms in place: delete the glossary and re-import, or edit terms individually.",
                          back=f"/glossary/{guid}")
        raise
    finally:
        os.unlink(path)


# ---------------------------------------------------------------- terms
@app.get("/glossary/{guid}/term/new", response_class=HTMLResponse)
def new_term(request: Request, guid: str):
    return render(request, "term_form.html", glossary=atlas().get_glossary(guid), term=None, active="glossary")


@app.post("/glossary/{guid}/term")
def create_term(guid: str, name: str = Form(...), short_description: str = Form(""),
                long_description: str = Form(""), abbreviation: str = Form(""), examples: str = Form("")):
    t = atlas().create_term(guid, name.strip(), short_description.strip(), long_description.strip(),
                            abbreviation.strip(), [e.strip() for e in examples.split(";") if e.strip()])
    return RedirectResponse(f"/term/{t['guid']}", status_code=303)


@app.get("/term/{guid}", response_class=HTMLResponse)
def term(request: Request, guid: str):
    a = atlas()
    t = a.get_term(guid)
    assigned = a.assigned_entities(guid)
    return render(request, "term.html", term=t, assigned=assigned, active="glossary")


@app.get("/term/{guid}/edit", response_class=HTMLResponse)
def edit_term(request: Request, guid: str):
    t = atlas().get_term(guid)
    return render(request, "term_form.html", term=t, glossary=t.get("anchor", {}), active="glossary")


@app.post("/term/{guid}")
def update_term(guid: str, name: str = Form(...), short_description: str = Form(""),
                long_description: str = Form(""), abbreviation: str = Form(""), examples: str = Form("")):
    atlas().update_term(guid, name=name.strip(), shortDescription=short_description.strip(),
                        longDescription=long_description.strip(), abbreviation=abbreviation.strip(),
                        examples=[e.strip() for e in examples.split(";") if e.strip()])
    return RedirectResponse(f"/term/{guid}", status_code=303)


@app.post("/term/{guid}/delete")
def delete_term(guid: str):
    a = atlas()
    gguid = a.get_term(guid).get("anchor", {}).get("glossaryGuid")
    a.delete_term(guid)
    return RedirectResponse(f"/glossary/{gguid}" if gguid else "/glossary", status_code=303)


@app.post("/term/{guid}/assign")
def assign(request: Request, guid: str, column: str = Form(...)):
    a = atlas()
    parts = column.strip().split(".")
    if len(parts) != 3:
        return render(request, "error.html", message="Enter the column as database.table.column", back=f"/term/{guid}")
    hit = a.find_column(*parts)
    if not hit:
        return render(request, "error.html", message=f"No column entity for {column} in Atlas. "
                      "Check the name, and that the table has been queried or created through Impala/Hive so the Atlas hook registered it.",
                      back=f"/term/{guid}")
    a.assign(guid, [hit])
    return RedirectResponse(f"/term/{guid}", status_code=303)


@app.post("/term/{guid}/unassign/{entity_guid}")
def unassign(guid: str, entity_guid: str):
    atlas().unassign(guid, entity_guid)
    return RedirectResponse(f"/term/{guid}", status_code=303)


# ---------------------------------------------------------------- runs
@app.get("/runs", response_class=HTMLResponse)
def runs(request: Request):
    return render(request, "runs.html", runs=runstore.list_runs(), runs_dir=runstore.RUNS_DIR, active="runs")


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(request: Request, run_id: str):
    s = runstore.summary(run_id)
    if not s["harvest"] and not s["profile"]:
        raise HTTPException(404, f"run {run_id} not found")
    profile = runstore.load(run_id, "profile") or {}
    harvest = runstore.load(run_id, "harvest") or {}
    tables = []
    for t in harvest.get("tables", []):
        key = f"{t['database']}.{t['table']}"
        prof = profile.get("tables", {}).get(key, {})
        tables.append({"key": key, "columns": len(t["columns"]), "row_count": t.get("row_count"),
                       "primary_keys": [pk["column"] for pk in prof.get("primary_keys", [])],
                       "profiled": bool(prof)})
    return render(request, "run.html", s=s, tables=tables, profile=profile, harvest=harvest, active="runs")


@app.get("/runs/{run_id}/table/{key}", response_class=HTMLResponse)
def run_table(request: Request, run_id: str, key: str):
    profile = runstore.load(run_id, "profile") or {}
    harvest = runstore.load(run_id, "harvest") or {}
    prof = profile.get("tables", {}).get(key)
    if not prof:
        raise HTTPException(404, f"{key} not profiled in run {run_id}")
    terms = {}
    for term in harvest.get("glossary_terms", []):
        for c in term["columns"]:
            terms.setdefault(c.split("@")[0], []).append(term["name"])
    rels = [r for r in profile.get("relationships", []) if r["from"] == key or r["to"] == key]
    return render(request, "run_table.html", run_id=run_id, key=key, prof=prof, terms=terms, rels=rels, active="runs")
