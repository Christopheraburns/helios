"""Feature code that core used to reach into, held here until its package exists.

- publication, review, graph: helios-model (burndown Phase 5)
- health: helios-serve (Phase 6)
- crawler, crawler_settings, evidence, browse: helios-crawl (Phase 10)

Nothing outside this directory in helios_core may import it; import-linter
enforces that (burndown 1.2). Each module leaves when its package is created.
"""
