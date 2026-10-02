"""Build an image-job manifest (JSONL) from TPC-DS, for the ComfyUI runner.

Run in Workbench (it reads TPC-DS through Impala, or a DuckDB TPC-DS file):

    PYTHONPATH=src:../../shared:$PYTHONPATH python images/build_manifest.py \\
        --pilot-per-category 5 --out images/manifests/pilot-50.jsonl

    ... --items AAAAAAAAEAAAAAAA,AAAAAAAACAAAAAAA --out images/manifests/some.jsonl
    ... --all --out images/manifests/all.jsonl
    ... --tpcds duckdb:/path/tpcds.duckdb   (instead of Impala)

Prompts come from images/class_objects.yaml and images/colors.yaml.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from helios_ds.backends import tpcds_from_uri
from helios_ds.images import (
    ITEM_SQL,
    VIEWS,
    UnmappedItem,
    jobs_for_item,
    load_class_visuals,
    load_colors,
    pilot_items,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--pilot-per-category", type=int, help="items per category (10 categories)")
    which.add_argument("--items", help="comma-separated i_item_id values")
    which.add_argument("--all", action="store_true", help="every mapped item (about 9,000)")
    parser.add_argument("--views", default=",".join(VIEWS), help=f"default {','.join(VIEWS)}")
    parser.add_argument("--tpcds", default="impala:tpcds", help="impala:tpcds or duckdb:/path")
    parser.add_argument("--size", type=int, default=1024, help="image width and height")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    rows = tpcds_from_uri(args.tpcds).query(ITEM_SQL)
    if args.pilot_per_category:
        items = pilot_items(rows, args.pilot_per_category)
    else:
        wanted = set(args.items.split(",")) if args.items else None
        items = pilot_items(
            (r for r in rows if wanted is None or r["i_item_id"] in wanted), per_category=10**9
        )
    visuals, colors = load_class_visuals(), load_colors()
    views = [v.strip() for v in args.views.split(",") if v.strip()]
    jobs, skipped = [], []
    for item in items:
        try:
            jobs += jobs_for_item(item, visuals, colors, views, args.size, args.size)
        except UnmappedItem as exc:
            skipped.append(str(exc))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(job.to_json() + "\n" for job in jobs))
    print(f"{len(jobs)} image jobs for {len(items) - len(skipped)} items -> {args.out}")
    print("by category:", dict(Counter(j.category for j in jobs if j.view == "front")))
    for line in skipped[:10]:
        print("skipped:", line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
