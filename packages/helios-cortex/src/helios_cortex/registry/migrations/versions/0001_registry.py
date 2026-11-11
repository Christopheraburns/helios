"""The model registry: data sources, models and their semantic revisions (burndown 4.2).

Revision ID: 0001_registry
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001_registry"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "helios_data_sources",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("connector", sa.String(255), nullable=False),
        sa.Column("connection_ref", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("snapshot_ids", sa.JSON, nullable=False),
        sa.Column("scope", sa.JSON, nullable=False),
        sa.Column("crawl", sa.JSON, nullable=False),
        sa.Column("updated_at", sa.String(64)),
        sa.Column("updated_by", sa.String(255)),
    )
    op.create_table(
        "helios_models",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("status", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("glossary_id", sa.String(255)),
        sa.Column("semantic_model_id", sa.String(255)),
        sa.Column("ontology_id", sa.String(255)),
        sa.Column("version_ids", sa.JSON, nullable=False),
        sa.Column("discovery_run_ids", sa.JSON, nullable=False),
    )
    op.create_table(
        "helios_model_data_sources",
        sa.Column("model_id", sa.String(255), sa.ForeignKey("helios_models.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("data_source_id", sa.String(255), sa.ForeignKey("helios_data_sources.id", ondelete="RESTRICT"),
                  primary_key=True),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("selected_assets", sa.JSON, nullable=False),
    )
    op.create_table(
        "helios_semantic_revisions",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("model_id", sa.String(255), sa.ForeignKey("helios_models.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("artifact_path", sa.Text, nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("ossie_version", sa.String(64)),
        sa.Column("discovery_run_id", sa.String(255)),
        sa.Column("published_by", sa.String(255)),
    )
    op.create_index("ix_helios_semantic_revisions_model_published", "helios_semantic_revisions",
                    ["model_id", "published_at"])


def downgrade() -> None:
    op.drop_index("ix_helios_semantic_revisions_model_published", "helios_semantic_revisions")
    op.drop_table("helios_semantic_revisions")
    op.drop_table("helios_model_data_sources")
    op.drop_table("helios_models")
    op.drop_table("helios_data_sources")
