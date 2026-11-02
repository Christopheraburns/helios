"""Domain action vocabulary for Helios resource authorization."""
from enum import Enum


class Action(str, Enum):
    ORGANIZATION_READ = "organization.read"
    ORGANIZATION_MANAGE = "organization.manage"

    DATASOURCE_READ = "datasource.read"
    DATASOURCE_MANAGE = "datasource.manage"

    MODEL_CREATE = "model.create"
    MODEL_READ = "model.read"
    MODEL_EDIT = "model.edit"
    MODEL_DELETE = "model.delete"
    MODEL_PUBLISH = "model.publish"

    DISCOVERY_RUN = "discovery.run"

    GLOSSARY_READ = "glossary.read"
    GLOSSARY_EDIT = "glossary.edit"

    SEMANTIC_READ = "semantic.read"
    SEMANTIC_EDIT = "semantic.edit"

    ONTOLOGY_READ = "ontology.read"
    ONTOLOGY_EDIT = "ontology.edit"

    QUERY_COMPILE = "query.compile"
    QUERY_EXECUTE = "query.execute"
