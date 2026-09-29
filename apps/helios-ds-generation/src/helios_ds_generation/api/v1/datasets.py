"""Dataset, artifact, crawl and evaluation endpoints.

No dataset can exist until generation is implemented (phase 1+), so lookups
return 404 and actions that belong to later tasks return 501.
"""

from typing import NoReturn

from fastapi import APIRouter, HTTPException

router = APIRouter()


def _dataset_not_found(dataset_id: str) -> NoReturn:
    raise HTTPException(status_code=404, detail=f"dataset {dataset_id} not found")


@router.get("/datasets/{dataset_id}")
async def get_dataset(dataset_id: str) -> dict:
    """Dataset metadata (F-09/F-10)."""
    _dataset_not_found(dataset_id)


@router.get("/datasets/{dataset_id}/manifest")
async def get_dataset_manifest(dataset_id: str) -> dict:
    """Public generation manifest (F-09)."""
    _dataset_not_found(dataset_id)


@router.get("/datasets/{dataset_id}/artifacts")
async def list_dataset_artifacts(dataset_id: str) -> list:
    """Artifact inventory (F-09)."""
    _dataset_not_found(dataset_id)


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str) -> dict:
    """Artifact metadata and retrievable locator (F-09)."""
    raise HTTPException(status_code=404, detail=f"artifact {artifact_id} not found")


@router.post("/datasets/{dataset_id}:crawl")
async def request_crawl(dataset_id: str) -> dict:
    """Integration hook to request a Helios crawl."""
    raise HTTPException(status_code=501, detail="crawl hook not implemented yet")


@router.post("/evaluations")
async def create_evaluation() -> dict:
    """Run golden evaluation (Helios-DS-Evaluation project)."""
    raise HTTPException(status_code=501, detail="evaluation not implemented yet")


@router.get("/evaluations/{evaluation_id}")
async def get_evaluation(evaluation_id: str) -> dict:
    raise HTTPException(status_code=404, detail=f"evaluation {evaluation_id} not found")
