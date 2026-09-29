"""FastAPI application for Helios-DS-Generation.

Serves both the REST API and static dashboard UI.
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .routers import jobs, config, results

app = FastAPI(
    title="Helios-DS-Generation",
    description="Deterministic synthetic enterprise data generator",
    version="0.1.0",
)

# Include API routers
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(config.router, prefix="/api/config", tags=["config"])
app.include_router(results.router, prefix="/api/results", tags=["results"])

# Mount static files (React dashboard)
dist_path = Path(__file__).parent.parent.parent.parent.parent / "ui" / "dist"
if dist_path.exists():
    app.mount("/", StaticFiles(directory=dist_path, html=True), name="static")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "helios-ds-generation"}
