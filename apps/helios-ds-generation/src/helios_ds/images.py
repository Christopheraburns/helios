"""Product image library: prompts and manifests (Helios-DS Phase 5 groundwork, V-00).

Images are made once, offline, by a diffusion model (Qwen-Image in ComfyUI on a
GPU workstation), then frozen as a versioned library. Diffusion output is not
byte-for-byte reproducible across GPUs, drivers or ComfyUI versions, so the
images are recorded inputs, not generated on every run: each one keeps its
prompt, seed, workflow and model, and the library version joins the identity of
any dataset that uses it.

Prompts are built only from TPC-DS's structured attributes (category, class,
color, size), never from item names or descriptions, which are random text. So
every image agrees with the warehouse facts it illustrates.

Per item there are three images by default:

- ``front``: generated from the text prompt;
- ``angle``: an edit of ``front`` showing the same product from another angle
  (editing one reference keeps the product consistent across views);
- ``damaged``: an edit of ``front`` showing the product's packaging crushed and
  torn, the visual evidence for the return story's PACKAGING_DAMAGED claim.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import yaml

LIBRARY = "tpcds-items"
MANIFEST_VERSION = 1
IMAGES_DIR = Path(__file__).resolve().parents[2] / "images"
VIEWS = ("front", "angle", "damaged")
SIZES = {"small", "medium", "large", "extra large", "petite"}

NEGATIVE_PROMPT = (
    "people, person, hands, fingers, text, letters, words, logo, watermark, "
    "blurry, low quality, deformed, duplicate product, cropped product"
)

# Revised after the first pilot images: a straight-on view of a plain product
# (a book face-on, no title) is an unrecognisable coloured rectangle. Products are
# shown at a three-quarter angle, and where text is natural (titles of books and
# albums) the TPC-DS product name is printed and recorded, so OCR can verify it.
FRONT_PROMPT = (
    "Professional e-commerce product photograph of {object}{size}{detail}, shown at a "
    "three-quarter angle so its shape, depth and edges are clearly visible. The product is "
    "centred on a plain light grey studio background with soft, even lighting, gentle shadows "
    "and sharp focus, and the whole product is in frame. {text_rule} No people, no hands, "
    "no watermark."
)
TITLE_RULE = (
    'The title "{label_text}" is printed on it in large, clear, correctly spelled letters, '
    "and there is no other text or logo."
)
NO_TEXT_RULE = "No text and no logos."
ANGLE_PROMPT = (
    "Show exactly the same product lying flat, seen from directly above. Keep the product's "
    "colour, shape, printed text and details identical, and keep the same plain light grey "
    "studio background and lighting. No people."
)
DAMAGED_PROMPT = (
    "Show exactly the same product partly visible inside {packaging}. The {packaging_noun} is "
    "visibly damaged: crushed on one corner and torn open, as if it had been dropped in "
    "transit. Keep the product's colour and details identical and keep the same plain light "
    "grey studio background. No people, no text."
)


@dataclass(frozen=True)
class ClassVisual:
    object: str  # contains {color}
    packaging: str
    sized: bool = False
    label: Optional[str] = None  # "title": print the product name as the title
    detail: Optional[str] = None  # a recognisability cue, e.g. cover art


def load_class_visuals(path: Optional[Path] = None) -> Dict[tuple, ClassVisual]:
    """(category, class) -> how it looks, from images/class_objects.yaml."""
    data = yaml.safe_load((path or IMAGES_DIR / "class_objects.yaml").read_text())
    return {
        (category, cls): ClassVisual(**entry)
        for category, classes in data.items()
        for cls, entry in classes.items()
    }


def load_colors(path: Optional[Path] = None) -> Dict[str, str]:
    return dict(yaml.safe_load((path or IMAGES_DIR / "colors.yaml").read_text()))


def _noun(phrase: str) -> str:
    """'a printed retail box' -> 'printed retail box'."""
    for article in ("a ", "an ", "the "):
        if phrase.startswith(article):
            return phrase[len(article) :]
    return phrase


def seed_for(item_id: str, view: str) -> int:
    """A stable 32-bit seed per item and view."""
    digest = hashlib.sha256(f"{LIBRARY}|v{MANIFEST_VERSION}|{item_id}|{view}".encode()).hexdigest()
    return int(digest[:8], 16)


@dataclass(frozen=True)
class ImageJob:
    """One image to make. ``kind`` is generate (text to image) or edit (of
    ``source_image_id``). Jobs for an item are ordered so the source comes first."""

    library: str
    manifest_version: int
    image_id: str
    item_id: str
    item_sk: int
    category: str
    item_class: str
    color: str
    size: Optional[str]
    view: str
    kind: str
    source_image_id: Optional[str]
    label_text: Optional[str]  # text the image must show (verified by OCR at ingestion)
    prompt: str
    negative_prompt: str
    seed: int
    width: int
    height: int

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


class UnmappedItem(ValueError):
    pass


def jobs_for_item(
    item: Dict[str, Any],
    visuals: Dict[tuple, ClassVisual],
    colors: Dict[str, str],
    views: Sequence[str] = VIEWS,
    width: int = 1024,
    height: int = 1024,
) -> List[ImageJob]:
    """The image jobs for one TPC-DS item row (i_item_id, i_item_sk, i_category,
    i_class, i_color, i_size)."""
    category, cls, color = item.get("i_category"), item.get("i_class"), item.get("i_color")
    visual = visuals.get((category, cls))
    if visual is None or not color or color not in colors:
        raise UnmappedItem(f"{item.get('i_item_id')}: no visual for {category}/{cls}/{color}")
    size = item.get("i_size") if visual.sized and item.get("i_size") in SIZES else None
    obj = visual.object.format(color=colors[color])
    label_text = None
    if visual.label == "title":
        label_text = str(item.get("i_product_name") or "").strip() or None
        if label_text is None:
            raise UnmappedItem(f"{item.get('i_item_id')}: no product name to print as the title")
    item_id = str(item["i_item_id"])
    common = {
        "library": LIBRARY,
        "manifest_version": MANIFEST_VERSION,
        "item_id": item_id,
        "item_sk": int(item["i_item_sk"]),
        "category": category,
        "item_class": cls,
        "color": color,
        "size": size,
        "negative_prompt": NEGATIVE_PROMPT,
        "width": width,
        "height": height,
    }
    front_id = f"{item_id}-front"
    prompts = {
        "front": FRONT_PROMPT.format(
            object=obj,
            size=f", in size {size}" if size else "",
            detail=f", with {visual.detail}" if visual.detail else "",
            text_rule=TITLE_RULE.format(label_text=label_text) if label_text else NO_TEXT_RULE,
        ),
        "angle": ANGLE_PROMPT,
        "damaged": DAMAGED_PROMPT.format(
            packaging=visual.packaging, packaging_noun=_noun(visual.packaging)
        ),
    }
    jobs = []
    for view in ("front", *[v for v in views if v != "front"]):
        if view not in prompts:
            raise ValueError(f"unknown view {view!r}")
        jobs.append(
            ImageJob(
                **common,
                image_id=f"{item_id}-{view}",
                view=view,
                kind="generate" if view == "front" else "edit",
                source_image_id=None if view == "front" else front_id,
                label_text=label_text,
                prompt=prompts[view],
                seed=seed_for(item_id, view),
            )
        )
    return jobs


def pilot_items(rows: Iterable[Dict[str, Any]], per_category: int) -> List[Dict[str, Any]]:
    """``per_category`` items per category, chosen by a stable hash of the item ID
    (one row per item: the current version). Rows without category/class are skipped."""
    current: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not row.get("i_category") or not row.get("i_class"):
            continue
        item_id = str(row["i_item_id"])
        # TPC-DS keeps price history: prefer the open-ended (current) row.
        if item_id not in current or row.get("i_rec_end_date") is None:
            current[item_id] = row
    by_category: Dict[str, List[Dict[str, Any]]] = {}
    for row in current.values():
        by_category.setdefault(str(row["i_category"]), []).append(row)
    chosen = []
    for category in sorted(by_category):
        ranked = sorted(
            by_category[category],
            key=lambda r: hashlib.sha256(f"pilot|{r['i_item_id']}".encode()).hexdigest(),
        )
        chosen += ranked[:per_category]
    return chosen


ITEM_SQL = (
    "SELECT i_item_sk, i_item_id, i_product_name, i_category, i_class, i_color, i_size, "
    "i_rec_end_date FROM item"
)
