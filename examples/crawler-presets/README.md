# Crawler presets

A preset is a complete crawler settings document for one kind of data: identifier
patterns, PDF labels, contextual phrases, case-linking rules and claim cues. The crawler
has no rules of its own (settings schema 2); load a preset, adjust it and save it as a
version:

```python
from helios_cortex._pending import crawler_settings
from helios_cortex._pending.crawler.settings import load_preset

settings = load_preset("examples/crawler-presets/retail-returns.yaml")
crawler_settings.save(index, settings, "you@example.com", "retail returns, as shipped")
```

`index.yaml` lists the presets here. `retail-returns.yaml` is the set the crawler was first
written with, for store returns (emails, chat threads and PDF return reports) over TPC-DS.
The crawler, and with it this API, becomes helios-crawl in Phase 10.
