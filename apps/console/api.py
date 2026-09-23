"""Authorized, model-scoped HTTP API for the Helios console application.

Authentication and HTTP error translation live here at the application boundary.
Resource policy remains in ``helios_core.authz`` and can be reused by MCP or jobs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Callable

from fastapi import APIRouter, Depends, HTTPException, Request

from helios_core import authz
from helios_core import runs as runstore
from helios_core.artifacts import ArtifactStore
from helios_core.domain import Model, Organization
from helios_core.graph import (
    ArtifactGraphRepository,
    GraphRepository,
    authorize_graph,
    graph_response,
)
from helios_core.metadata import MetadataRepository

api_router = APIRouter(prefix="/api/v1", tags=["api-v1"])


class ResourceStore:
    """Small application-boundary store, replaceable by persistent storage later."""

    def __init__(
        self,
        organizations: tuple[Organization, ...] = (),
        models: tuple[Model, ...] = (),
    ):
        self._organizations = {organization.id: organization for organization in organizations}
        self._models = {model.id: model for model in models}
        for model in models:
            if model.organization_id not in self._organizations:
                raise ValueError(
                    f"model {model.id!r} references unknown organization "
                    f"{model.organization_id!r}"
                )

    def organization(self, organization_id: str) -> Organization | None:
        return self._organizations.get(organization_id)

    def model(self, model_id: str) -> Model | None:
        return self._models.get(model_id)

    def models_for_organization(self, organization_id: str) -> list[Model]:
        return sorted(
            (
                model
                for model in self._models.values()
                if model.organization_id == organization_id
            ),
            key=lambda model: model.name.lower(),
        )


@dataclass(frozen=True)
class AuthorizedModel:
    model: Model
    principal: authz.Principal
    policy: authz.Policy

    @property
    def available_actions(self) -> list[str]:
        return [
            action.value
            for action in authz.Action
            if self.policy.can(
                self.principal,
                action,
                _resource_for_action(self.model, action),
            ).allowed
        ]


def current_principal(request: Request) -> authz.Principal:
    """Translate trusted Workbench identity context into a core Principal."""
    subject = request.headers.get("x-forwarded-user")
    if not subject:
        raise HTTPException(401, "authenticated principal is required")
    return authz.Principal(
        issuer="cloudera-workbench",
        subject=subject,
        kind=authz.PrincipalKind.HUMAN,
        display_name=subject,
    )


def resource_store(request: Request) -> ResourceStore | MetadataRepository:
    override = getattr(request.app.state, "resource_store", None)
    return override or request.app.state.metadata_repository


def authorization_policy(
    request: Request,
    principal: Annotated[authz.Principal, Depends(current_principal)],
) -> authz.Policy:
    override = getattr(request.app.state, "authorization_policy", None)
    if override is not None:
        return override
    repository: MetadataRepository = request.app.state.metadata_repository
    return authz.Policy(repository.grants_for_principal(principal.id))


def graph_repository(request: Request) -> GraphRepository:
    override = getattr(request.app.state, "graph_repository", None)
    return override or ArtifactGraphRepository(ArtifactStore(runstore.ROOT))


def load_organization(
    org_id: str,
    store: Annotated[ResourceStore | MetadataRepository, Depends(resource_store)],
) -> Organization:
    organization = store.organization(org_id)
    if organization is None:
        raise HTTPException(404, "organization not found")
    return organization


def load_model(
    model_id: str,
    store: Annotated[ResourceStore | MetadataRepository, Depends(resource_store)],
) -> Model:
    model = store.model(model_id)
    if model is None:
        raise HTTPException(404, "model not found")
    return model


def authorize_organization(
    action: authz.Action,
) -> Callable[..., Organization]:
    def dependency(
        organization: Annotated[Organization, Depends(load_organization)],
        principal: Annotated[authz.Principal, Depends(current_principal)],
        policy: Annotated[authz.Policy, Depends(authorization_policy)],
    ) -> Organization:
        resource = authz.Resource(
            "organization", organization.id, organization_id=organization.id
        )
        _require(policy, principal, action, resource)
        return organization

    return dependency


def authorize_model(
    action: authz.Action,
    resource_type: str = "model",
) -> Callable[..., AuthorizedModel]:
    def dependency(
        model: Annotated[Model, Depends(load_model)],
        principal: Annotated[authz.Principal, Depends(current_principal)],
        policy: Annotated[authz.Policy, Depends(authorization_policy)],
    ) -> AuthorizedModel:
        resource_id = model.id if resource_type == "model" else (
            getattr(model, f"{resource_type}_id", None) or resource_type
        )
        resource = authz.Resource(
            resource_type,
            resource_id,
            organization_id=model.organization_id,
            model_id=None if resource_type == "model" else model.id,
        )
        _require(policy, principal, action, resource)
        return AuthorizedModel(model, principal, policy)

    return dependency


def _require(
    policy: authz.Policy,
    principal: authz.Principal,
    action: authz.Action,
    resource: authz.Resource,
) -> None:
    try:
        policy.require(principal, action, resource)
    except authz.AuthorizationDenied as exc:
        raise HTTPException(403, str(exc)) from exc


def _resource_for_action(model: Model, action: authz.Action) -> authz.Resource:
    prefix = action.value.split(".", 1)[0]
    if prefix == "organization":
        return authz.Resource(
            "organization", model.organization_id, model.organization_id
        )
    if prefix == "datasource":
        return authz.Resource(
            "datasource", "referenced", model.organization_id, model.id
        )
    if prefix in {"model", "discovery", "query"}:
        return authz.Resource("model", model.id, model.organization_id)
    resource_id = getattr(model, f"{prefix}_id", None) or prefix
    return authz.Resource(prefix, resource_id, model.organization_id, model.id)


def _model_metadata(context: AuthorizedModel) -> dict:
    model = context.model
    return {
        "id": model.id,
        "organization_id": model.organization_id,
        "name": model.name,
        "description": model.description,
        "data_sources": [
            {
                "data_source_id": reference.data_source_id,
                "selected_assets": list(reference.selected_assets),
            }
            for reference in model.data_sources
        ],
        "available_actions": context.available_actions,
    }


@api_router.get("/organizations/{org_id}")
def get_organization(
    organization: Annotated[
        Organization,
        Depends(authorize_organization(authz.Action.ORGANIZATION_READ)),
    ],
) -> dict:
    return {
        "id": organization.id,
        "name": organization.name,
        "member_ids": list(organization.member_ids),
    }


@api_router.get("/organizations/{org_id}/models")
def list_organization_models(
    organization: Annotated[
        Organization,
        Depends(authorize_organization(authz.Action.ORGANIZATION_READ)),
    ],
    store: Annotated[ResourceStore | MetadataRepository, Depends(resource_store)],
    principal: Annotated[authz.Principal, Depends(current_principal)],
    policy: Annotated[authz.Policy, Depends(authorization_policy)],
) -> dict:
    return {
        "organization_id": organization.id,
        "models": [
            _model_metadata(AuthorizedModel(model, principal, policy))
            for model in store.models_for_organization(organization.id)
        ],
    }


@api_router.get("/models/{model_id}")
def get_model(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.MODEL_READ)),
    ],
) -> dict:
    return _model_metadata(context)


@api_router.get("/models/{model_id}/graph")
def get_model_graph(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.MODEL_READ)),
    ],
    repository: Annotated[GraphRepository, Depends(graph_repository)],
) -> dict:
    graph = repository.graph_for_model(context.model)
    try:
        authorized = authorize_graph(
            graph, context.model, context.principal, context.policy
        )
    except ValueError as exc:
        raise HTTPException(404, "model graph not found") from exc
    return graph_response(authorized)


@api_router.get("/models/{model_id}/glossary")
def get_model_glossary(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.GLOSSARY_READ, "glossary")),
    ],
) -> dict:
    return {
        "model_id": context.model.id,
        "glossary_id": context.model.glossary_id,
        "available_actions": context.available_actions,
    }


@api_router.get("/models/{model_id}/semantic")
def get_model_semantic(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.SEMANTIC_READ, "semantic")),
    ],
) -> dict:
    return {
        "model_id": context.model.id,
        "semantic_model_id": context.model.semantic_model_id,
        "available_actions": context.available_actions,
    }


@api_router.get("/models/{model_id}/ontology")
def get_model_ontology(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.ONTOLOGY_READ, "ontology")),
    ],
) -> dict:
    return {
        "model_id": context.model.id,
        "ontology_id": context.model.ontology_id,
        "available_actions": context.available_actions,
    }


@api_router.get("/models/{model_id}/runs")
def get_model_runs(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.MODEL_READ)),
    ],
) -> dict:
    known = {run["id"]: run for run in runstore.list_runs()}
    return {
        "model_id": context.model.id,
        "runs": [
            known.get(run_id, {"id": run_id, "stages": {}, "missing": True})
            for run_id in context.model.discovery_run_ids
        ],
        "available_actions": context.available_actions,
    }


@api_router.get("/models/{model_id}/versions")
def get_model_versions(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.SEMANTIC_READ, "semantic")),
    ],
) -> dict:
    return {
        "model_id": context.model.id,
        "versions": list(context.model.version_ids),
        "available_actions": context.available_actions,
    }
