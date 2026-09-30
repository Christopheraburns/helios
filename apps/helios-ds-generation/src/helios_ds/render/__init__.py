"""Deterministic artifact rendering (phase 3+)."""

from .base import COMPANY, Case, Mention, RenderContext, RenderedArtifact, TextBuilder
from .registry import has_renderer, render_artifact, renderer_for

__all__ = [
    "COMPANY",
    "Case",
    "Mention",
    "RenderContext",
    "RenderedArtifact",
    "TextBuilder",
    "has_renderer",
    "render_artifact",
    "renderer_for",
]
