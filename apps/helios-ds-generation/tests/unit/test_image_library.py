"""V-00: image prompts, manifests and the ComfyUI runner (against a fake ComfyUI)."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from helios_ds.images import (
    VIEWS,
    UnmappedItem,
    jobs_for_item,
    load_class_visuals,
    load_colors,
    pilot_items,
    seed_for,
)

IMAGES = Path(__file__).resolve().parents[2] / "images"
ITEM = {
    "i_item_sk": 5,
    "i_item_id": "AAAAAAAAEAAAAAAA",
    "i_category": "Men",
    "i_class": "pants",
    "i_color": "moccasin",
    "i_size": "large",
    "i_rec_end_date": None,
}


def _runner():
    spec = importlib.util.spec_from_file_location("comfyui_runner", IMAGES / "comfyui_runner.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["comfyui_runner"] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


def test_tables_cover_every_tpcds_class_and_color():
    visuals, colors = load_class_visuals(), load_colors()
    assert len(visuals) == 100 and len(colors) == 92
    assert all("{color}" in v.object for v in visuals.values())


def test_jobs_follow_the_structured_facts():
    jobs = jobs_for_item(ITEM, load_class_visuals(), load_colors())
    assert [j.view for j in jobs] == list(VIEWS)
    front, angle, damaged = jobs
    assert "moccasin tan men's trousers" in front.prompt and "in size large" in front.prompt
    assert (front.kind, angle.kind, damaged.kind) == ("generate", "edit", "edit")
    assert angle.source_image_id == damaged.source_image_id == front.image_id
    assert "clear plastic retail bag is visibly damaged" in damaged.prompt
    assert front.seed == seed_for(ITEM["i_item_id"], "front") != angle.seed


def test_unsized_classes_ignore_size_and_unknown_items_are_refused():
    music = {
        **ITEM,
        "i_category": "Music",
        "i_class": "pop",
        "i_size": "large",
        "i_product_name": "ought",
    }
    assert "in size" not in jobs_for_item(music, load_class_visuals(), load_colors())[0].prompt
    with pytest.raises(UnmappedItem):
        jobs_for_item({**ITEM, "i_class": None}, load_class_visuals(), load_colors())


def test_pilot_selection_is_stable_balanced_and_uses_current_rows():
    rows = [
        {**ITEM, "i_item_id": f"ID{i:03d}", "i_item_sk": i, "i_category": cat}
        for i, cat in enumerate(["Men", "Women"] * 10)
    ]
    rows.insert(0, {**rows[0], "i_item_sk": 999, "i_rec_end_date": "2000-01-01"})  # older version
    rows.append({**ITEM, "i_item_id": "NOCLASS", "i_class": None})
    picked = pilot_items(rows, per_category=3)
    assert len(picked) == 6 and {r["i_category"] for r in picked} == {"Men", "Women"}
    assert picked == pilot_items(list(reversed(rows)), per_category=3)
    assert all(r["i_item_sk"] != 999 for r in picked)


def test_pilot_manifest_is_valid():
    lines = (IMAGES / "manifests/pilot-50.jsonl").read_text().splitlines()
    jobs = [json.loads(line) for line in lines]
    assert len(jobs) == 150 and len({j["image_id"] for j in jobs}) == 150
    by_item: dict = {}
    for job in jobs:
        by_item.setdefault(job["item_id"], []).append(job["view"])
    assert all(views == list(VIEWS) for views in by_item.values())  # front comes first


# --- the runner, against a fake ComfyUI ---------------------------------------------------


class FakeComfy:
    def __init__(self, fail_first=0):
        self.queued, self.uploads, self.fail_first = [], [], fail_first

    def system_stats(self):
        return {"system": {"comfyui_version": "test"}}

    def queue(self, workflow):
        if self.fail_first:
            self.fail_first -= 1
            raise RuntimeError("out of memory")
        self.queued.append(workflow)
        return f"p{len(self.queued)}"

    def history(self, prompt_id):
        return {
            "status": {"completed": True},
            "outputs": {"9": {"images": [{"filename": f"{prompt_id}.png"}]}},
        }

    def download(self, image):
        return f"PNG:{image['filename']}".encode()

    def upload(self, data, filename):
        self.uploads.append(filename)
        return filename


@pytest.fixture
def config(tmp_path):
    runner = _runner()
    gen = {
        "1": {"class_type": "Text", "inputs": {"text": ""}},
        "2": {"class_type": "KSampler", "inputs": {"seed": 0}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x"}},
    }
    edit = {**gen, "4": {"class_type": "LoadImage", "inputs": {"image": ""}}}
    (tmp_path / "gen.json").write_text(json.dumps(gen))
    (tmp_path / "edit.json").write_text(json.dumps(edit))
    (tmp_path / "cfg.json").write_text(
        json.dumps(
            {
                "output_dir": "out",
                "poll_seconds": 0,
                "attempts": 2,
                "workflows": {
                    "generate": {
                        "file": "gen.json",
                        "output_node": "9",
                        "inputs": {
                            "prompt": ["1", "text"],
                            "seed": ["2", "seed"],
                            "filename_prefix": ["9", "filename_prefix"],
                        },
                    },
                    "edit": {
                        "file": "edit.json",
                        "output_node": "9",
                        "inputs": {
                            "prompt": ["1", "text"],
                            "seed": ["2", "seed"],
                            "image": ["4", "image"],
                        },
                    },
                },
            }
        )
    )
    return runner, runner.load_config(tmp_path / "cfg.json")


def _jobs():
    return [
        json.loads(j.to_json()) for j in jobs_for_item(ITEM, load_class_visuals(), load_colors())
    ]


def _quiet(runner, client, cfg, jobs):
    return runner.run(client, cfg, jobs, log=lambda m: None, sleep=lambda s: None)


def test_runner_makes_images_with_sidecars_and_resumes(config):
    runner, cfg = config
    fake = FakeComfy()
    assert _quiet(runner, fake, cfg, _jobs()) == {"made": 3, "skipped": 0, "failed": 0}
    generate, angle, _ = fake.queued
    assert generate["1"]["inputs"]["text"].startswith("Professional e-commerce product photograph")
    assert generate["2"]["inputs"]["seed"] == seed_for(ITEM["i_item_id"], "front")
    # The angle view is an edit of the front image.
    assert angle["4"]["inputs"]["image"] == f"{ITEM['i_item_id']}-front.png"
    folder = cfg.output_dir / ITEM["i_item_id"]
    sidecar = json.loads((folder / f"{ITEM['i_item_id']}-front.json").read_text())
    data = (folder / f"{ITEM['i_item_id']}-front.png").read_bytes()
    assert sidecar["sha256"] == hashlib.sha256(data).hexdigest()
    assert sidecar["workflow"] == "generate" and sidecar["seed"] == generate["2"]["inputs"]["seed"]
    assert _quiet(runner, FakeComfy(), cfg, _jobs()) == {"made": 0, "skipped": 3, "failed": 0}
    assert runner.status(cfg, _jobs())["done"] == 3


def test_runner_retries_then_records_failures(config):
    runner, cfg = config
    assert _quiet(runner, FakeComfy(fail_first=1), cfg, _jobs()[:1])["made"] == 1  # retried
    cfg.output_dir = cfg.output_dir.parent / "out2"
    counts = _quiet(runner, FakeComfy(), cfg, _jobs()[1:2])
    assert counts["failed"] == 1  # the angle edit has no front image yet
    error = cfg.output_dir / ITEM["i_item_id"] / f"{ITEM['i_item_id']}-angle.error.json"
    assert "has not been made yet" in json.loads(error.read_text())["error"]


def test_config_rejects_a_mapping_to_a_missing_node(tmp_path, config):
    runner, _ = config
    cfg = json.loads((tmp_path / "cfg.json").read_text())
    cfg["workflows"]["generate"]["inputs"]["width"] = ["99", "width"]
    (tmp_path / "bad.json").write_text(json.dumps(cfg))
    with pytest.raises(SystemExit, match="node '99'"):
        runner.load_config(tmp_path / "bad.json")


def test_config_explains_a_missing_or_ui_format_workflow(tmp_path, config):
    runner, _ = config
    cfg = json.loads((tmp_path / "cfg.json").read_text())
    cfg["workflows"]["edit"]["file"] = "not_exported.json"
    (tmp_path / "missing.json").write_text(json.dumps(cfg))
    with pytest.raises(SystemExit, match="workflow file not found"):
        runner.load_config(tmp_path / "missing.json")
    (tmp_path / "ui.json").write_text(json.dumps({"nodes": [], "links": []}))
    cfg["workflows"]["edit"]["file"] = "ui.json"
    (tmp_path / "uicfg.json").write_text(json.dumps(cfg))
    with pytest.raises(SystemExit, match="Export \\(API\\)"):
        runner.load_config(tmp_path / "uicfg.json")


def test_books_and_music_print_the_product_name_as_a_recorded_title():
    book = {**ITEM, "i_category": "Books", "i_class": "cooking", "i_product_name": "ableought"}
    front, angle, _ = jobs_for_item(book, load_class_visuals(), load_colors())
    assert 'The title "ableought" is printed on it' in front.prompt
    assert "pasta dish" in front.prompt and "three-quarter angle" in front.prompt
    assert front.label_text == angle.label_text == "ableought"
    trousers = jobs_for_item(ITEM, load_class_visuals(), load_colors())[0]
    assert trousers.label_text is None and "No text and no logos." in trousers.prompt
    with pytest.raises(UnmappedItem, match="no product name"):
        jobs_for_item({**book, "i_product_name": None}, load_class_visuals(), load_colors())


def test_an_image_made_from_an_older_prompt_is_redone(config):
    runner, cfg = config
    jobs = _jobs()[:1]
    assert _quiet(runner, FakeComfy(), cfg, jobs)["made"] == 1
    changed = [{**jobs[0], "prompt": jobs[0]["prompt"] + " Revised."}]
    assert _quiet(runner, FakeComfy(), cfg, changed) == {"made": 1, "skipped": 0, "failed": 0}
