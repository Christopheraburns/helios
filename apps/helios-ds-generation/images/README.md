# Helios-DS product image library

Product photos for TPC-DS items, made once on a GPU workstation with ComfyUI and
Qwen-Image, then frozen as a versioned library that the Helios-DS image generator
(Phase 5, V-01) draws from. Background and decisions: Helios-DS burn-down, tasks V-00a to V-00f.

## Why a frozen library

Diffusion output is not byte-for-byte reproducible across GPUs, drivers or ComfyUI
versions, while Helios-DS datasets must be reproducible. So the images are made once,
each with a sidecar recording its prompt, seed, workflow and timing. The finished set
is ingested and published as a library version, and datasets that use it record that version.

## What gets made

Per item, three images (`--views` to change):

| View | How | Purpose |
|---|---|---|
| `front` | Qwen-Image, text to image | the product, studio shot |
| `angle` | Qwen-Image-Edit of `front` | the same product from a three-quarter angle (editing one reference keeps it consistent) |
| `damaged` | Qwen-Image-Edit of `front` | the product in its packaging, crushed and torn: visual evidence for the return story's PACKAGING_DAMAGED claim |

Prompts use only TPC-DS's structured attributes, via two reviewed tables:
- `class_objects.yaml`: what each of the 100 category/class pairs looks like, its packaging, and whether size applies;
- `colors.yaml`: the 92 TPC-DS color words, made drawable.

Item names and descriptions are random text, so they are never used.

## Files

| File | Runs where | What |
|---|---|---|
| `class_objects.yaml`, `colors.yaml` | | Prompt tables (draft for review) |
| `build_manifest.py` | Workbench | TPC-DS → JSONL image-job manifest |
| `manifests/pilot-50.jsonl` | | 50 items (5 per category) × 3 views = 150 jobs |
| `comfyui_runner.py` | Workstation | Drives ComfyUI; standard library only; resumable |
| `runner_config.example.json` | Workstation | Server, output folder, and workflow node mapping |
| `workflows/` | Workstation | The two ComfyUI workflows, exported in API format |

## Setup (on the workstation, or on any machine that can reach ComfyUI)

1. Install Python 3.9 or later; no packages are needed.
2. Copy this `images/` folder to the machine that will run the runner.
3. In ComfyUI, export the Qwen-Image workflow and the Qwen-Image-Edit workflow in **API format** into `workflows\` (see `workflows/README.md`).
4. Find the node IDs that receive each value:
   ```
   python comfyui_runner.py inspect workflows\qwen_image_api.json
   python comfyui_runner.py inspect workflows\qwen_image_edit_api.json
   ```
5. Copy `runner_config.example.json` to `runner_config.json` and put those node IDs in `inputs`:
   - prompt, negative prompt, seed, width and height (text-to-image), and the filename prefix;
   - for the edit workflow, the LoadImage `image` input;
   - `output_node`: the SaveImage node.

   Leave model, sampler, steps and CFG as set in the workflows: the workflow file's hash is recorded with every image.

## Running from another machine (e.g. a MacBook)

The runner only talks to ComfyUI's HTTP API, so it can run on any machine that can open the
ComfyUI web page, such as a MacBook on the same network. The GPU work still happens on the
workstation, but the images land on the machine running the runner, which makes the return
trip to Workbench shorter.

- Set `"server"` in `runner_config.json` to the address you use in the browser, e.g. `"http://192.168.1.50:8188"`.
- Python 3.9 or later; no packages.
- Export the two workflows from the ComfyUI web page on that machine (they download locally).
- Keep the machine awake and on the network for overnight runs. On macOS:
  `caffeinate -i python3 comfyui_runner.py run --manifest manifests/pilot-50.jsonl --config runner_config.json`
  If it sleeps, the run stops; rerunning resumes where it left off.
- ComfyUI has no login: keep it on a trusted network, never exposed to the internet.

## Running

```
python comfyui_runner.py run --manifest manifests\pilot-50.jsonl --config runner_config.json --limit 6
python comfyui_runner.py status --manifest manifests\pilot-50.jsonl --config runner_config.json
```

- Start with `--limit 6` (two items, all three views) to check the mapping and the results.
- Then run without a limit overnight. It skips finished images, so stopping and restarting is safe.
- Failures are written next to the image as `<image_id>.error.json` and retried on the next run.
- `status` reports progress, average seconds per image and an estimate of the hours left. Use the pilot's average to decide how far to scale (all ~9,000 items × 3 views ≈ 27,000 images).

Output layout: `output/tpcds-items-v1/<item_id>/<image_id>.png` plus `<image_id>.json`, and `server.json` (ComfyUI and GPU details).

## Next (not built yet)

- **Ingestion Job (V-00e):** from Workbench, verify the images against their sidecars, run the quality checks (V-00f), upload them to the S3 library prefix, and record the library version in the lakehouse. The workstation has no CDP credentials, so files reach Workbench by copy.
- **Quality checks (V-00f):** an automatic check that each image matches its class and color, plus human review of a sample.
