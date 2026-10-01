"""Shared crawler definitions: settings (CR-0e). The crawler itself runs as a
Workbench Job in the Helios project (apps/helios/crawler)."""

from .settings import DEFAULT_SETTINGS, CrawlerSettings, ontology_problems

__all__ = ["DEFAULT_SETTINGS", "CrawlerSettings", "ontology_problems"]
