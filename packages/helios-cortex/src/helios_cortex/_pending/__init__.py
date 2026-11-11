"""Feature code that core used to reach into, held here until its package exists.

- crawler, crawler_settings, evidence, browse: helios-crawl (Phase 10)

The model-discovery code that waited here (publication, review) moved to helios-model
in burndown 5.3, and health to the console in 2.6, so only crawl code is left.

Nothing outside this directory in helios_cortex may import it; import-linter
enforces that (burndown 1.2). Each module leaves when its package is created.
"""
