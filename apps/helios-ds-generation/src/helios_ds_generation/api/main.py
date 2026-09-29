"""FastAPI application for Helios-DS-Generation.

Serves both the REST API and static dashboard UI.
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import v1

app = FastAPI(
    title="Helios-DS-Generation",
    description="Deterministic synthetic enterprise data generator",
    version="0.1.0",
)

app.include_router(v1.router)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "helios-ds-generation"}


# Mount static files (React dashboard) last so it doesn't shadow API routes
dist_path = Path(__file__).parents[3] / "ui" / "dist"
if dist_path.exists():
    app.mount("/", StaticFiles(directory=dist_path, html=True), name="static")
