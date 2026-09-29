"""Helios-DS REST API, version 1."""

from fastapi import APIRouter

from . import config, datasets, jobs

router = APIRouter(prefix="/v1")
router.include_router(jobs.router, tags=["jobs"])
router.include_router(datasets.router, tags=["datasets"])
router.include_router(config.router, tags=["config"])
