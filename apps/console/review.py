"""Review and publish routes for the helios console.

Mounted from main.py with `app.include_router(review_router)`. Reads runs/<run>/propose.json, keeps decisions in
runs/<run>/review.json, and publishes the accepted subset beneath models/<model-id>/published/.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from helios_core import __version__
from helios_core import publication
from helios_core import review as rv
from helios_core import runs as runstore
from helios_core.artifacts import ArtifactStore, model_id_for_run
from helios_core.ossie import preflight

HERE = Path(__file__).parent
tpl = Jinja2Templates(directory=HERE / "templates")
review_router = APIRouter()

artifact_store = ArtifactStore(runstore.ROOT)
ROLES = ("identifier", "foreign_key", "time", "measure", "dimension", "attribute")
KINDS = ("fact", "dimension", "bridge", "lookup", "other")


def _render(request: Request, name: str, **ctx) -> HTMLResponse:
    ctx.setdefault("version", __version__)
    ctx.setdefault("error", None)
    return tpl.TemplateResponse(request, name, ctx)


def _load(run_id: str) -> tuple[dict, dict, str]:
    prop = runstore.load(run_id, "propose")
    if not prop:
        raise HTTPException(404, f"run {run_id} has no propose output yet")
    path = os.path.join(runstore.RUNS_DIR, run_id, "review.json")
    return prop, rv.load(path, run_id), path


def _published_model_name(prop: dict, run_id: str) -> str:
    h = runstore.load(run_id, "harvest") or {}
    return publication.model_name_for_proposal(prop, h)


def _model_id(prop: dict, run_id: str) -> str:
    harvest = runstore.load(run_id, "harvest") or {}
    return model_id_for_run(
        prop,
        harvest,
        legacy_default=_published_model_name(prop, run_id),
    )


# ---------------------------------------------------------------- page
@review_router.get("/runs/{run_id}/review", response_class=HTMLResponse)
def review_page(request: Request, run_id: str, published: str = "", errors: str = ""):
    prop, review, _ = _load(run_id)
    model_id = _model_id(prop, run_id)
    model_name = _published_model_name(prop, run_id)
    published_path = artifact_store.published_ossie_path(model_id)
    last_published = None
    if os.path.exists(published_path):
        last_published = datetime.fromtimestamp(os.path.getmtime(published_path), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return _render(request, "review.html", run_id=run_id, model_id=model_id, p=prop, review=review,
                   decisions=rv.decisions_by_id(review), entries=review,
                   summary=rv.summary(review, prop), roles=ROLES, kinds=KINDS,
                   model_name=model_name, last_published=last_published, preflight=preflight(prop),
                   published=published, errors=json.loads(errors) if errors else [],
                   rid=rv, active="runs")


# ---------------------------------------------------------------- decisions
@review_router.post("/runs/{run_id}/review/decide")
async def decide(request: Request, run_id: str):
    """JSON body: {section, id, decision, overrides?, note?}. Returns the updated summary."""
    prop, review, path = _load(run_id)
    body = await request.json()
    try:
        rv.decide(review, body["section"], body["id"], body["decision"], body.get("overrides") or None, body.get("note", ""))
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    rv.save(path, review, reviewed_by=request.headers.get("x-forwarded-user") or os.environ.get("CDSW_USER"))
    return JSONResponse({"ok": True, "summary": rv.summary(review, prop),
                         "entry": review[body["section"]][body["id"]]})


@review_router.post("/runs/{run_id}/review/dataset")
async def decide_dataset(request: Request, run_id: str):
    """JSON body: {table, decision}. Applies to the dataset and all of its fields."""
    prop, review, path = _load(run_id)
    body = await request.json()
    try:
        n = rv.cascade_dataset(review, prop, body["table"], body["decision"])
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    rv.save(path, review, reviewed_by=request.headers.get("x-forwarded-user") or os.environ.get("CDSW_USER"))
    return JSONResponse({"ok": True, "changed": n, "summary": rv.summary(review, prop),
                         "decisions": rv.decisions_by_id(review)})


@review_router.post("/runs/{run_id}/review/bulk")
def bulk(request: Request, run_id: str, min_confidence: float = Form(0.85)):
    prop, review, path = _load(run_id)
    rv.bulk_accept(review, prop, min_confidence)
    rv.save(path, review, reviewed_by=request.headers.get("x-forwarded-user") or os.environ.get("CDSW_USER"))
    return RedirectResponse(f"/runs/{run_id}/review", status_code=303)


@review_router.post("/runs/{run_id}/review/reset")
def reset(request: Request, run_id: str, section: str = Form("")):
    prop, review, path = _load(run_id)
    rv.clear(review, section or None)
    rv.save(path, review)
    return RedirectResponse(f"/runs/{run_id}/review", status_code=303)


# ---------------------------------------------------------------- publish
@review_router.post("/runs/{run_id}/publish")
def publish(request: Request, run_id: str, commit: str = Form("")):
    prop, review, _ = _load(run_id)
    model_id = _model_id(prop, run_id)
    model_name = _published_model_name(prop, run_id)
    try:
        result = publication.publish_reviewed_proposal(
            artifact_store,
            model_id=model_id,
            model_name=model_name,
            run_id=run_id,
            proposal=prop,
            review=review,
        )
    except publication.PublicationValidationError as exc:
        errors = list(exc.errors)
        return RedirectResponse(
            f"/runs/{run_id}/review?errors={json.dumps(errors[:20])}",
            status_code=303,
        )

    manifest = result.manifest
    msg = (
        f"published {model_name} ({model_id}): "
        f"{manifest['datasets']} datasets, "
        f"{manifest['relationships']} relationships, "
        f"{manifest['metrics']} metrics"
    )
    if commit:
        try:
            subprocess.run(["git", "add", f"models/{model_id}", f"runs/{run_id}/review.json"], cwd=runstore.ROOT, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", f"publish {model_id} from run {run_id}"], cwd=runstore.ROOT, check=True, capture_output=True)
            msg += "; committed"
        except subprocess.CalledProcessError as e:
            msg += f"; git commit failed: {(e.stderr or b'').decode()[:200]}"
    return RedirectResponse(f"/runs/{run_id}/review?published={msg}", status_code=303)
