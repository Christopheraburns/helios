"""Overnight image generation with ComfyUI, for the Helios-DS image library.

Runs anywhere that can reach ComfyUI over HTTP (Windows, macOS or Linux;
Python 3.9+, standard library only). It reads an image-job manifest (JSONL,
from build_manifest.py), drives a ComfyUI server through its HTTP API, and
writes each image with a JSON sidecar recording exactly how it was made.
Interrupted runs resume where they stopped: finished images (sidecar present,
hash matching) are skipped.

    python comfyui_runner.py inspect workflows\\qwen_image_api.json
    python comfyui_runner.py run --manifest manifests\\pilot-50.jsonl --config runner_config.json
    python comfyui_runner.py run ... --limit 9 --views front
    python comfyui_runner.py status --manifest manifests\\pilot-50.jsonl --config runner_config.json

Workflows must be exported from ComfyUI in API format, and runner_config.json
says which node inputs receive the prompt, seed, size and so on (see
runner_config.example.json and README.md).
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

RUNNER_VERSION = "1.0.0"


# --- configuration -------------------------------------------------------------------


@dataclass
class WorkflowConfig:
    name: str
    path: Path
    workflow: Dict[str, Any]
    inputs: Dict[str, List[str]]  # field -> [node_id, input_name]
    output_node: Optional[str]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


@dataclass
class Config:
    server: str
    output_dir: Path
    workflows: Dict[str, WorkflowConfig]  # "generate" and "edit"
    timeout_seconds: float = 900.0
    poll_seconds: float = 2.0
    attempts: int = 2


def load_config(path: Path) -> Config:
    raw = json.loads(path.read_text())
    base = path.parent
    workflows = {}
    for kind, spec in raw["workflows"].items():
        wf_path = (base / spec["file"]).expanduser().resolve()
        if not wf_path.is_file():
            raise SystemExit(
                f"{kind} workflow file not found: {wf_path}\n"
                f"Export the workflow from ComfyUI in API format and either save it there, or set "
                f'"file" for "{kind}" in {path.name} to where it is. To run only the front views '
                f'for now, remove the "edit" block and use --views front.'
            )
        workflow = json.loads(wf_path.read_text())
        if "nodes" in workflow and "links" in workflow:
            raise SystemExit(
                f"{wf_path.name} is a UI-format workflow; export it with Workflow → Export (API)."
            )
        workflows[kind] = WorkflowConfig(
            name=kind,
            path=wf_path,
            workflow=workflow,
            inputs={k: list(v) for k, v in spec["inputs"].items()},
            output_node=spec.get("output_node"),
        )
        for field, (node_id, input_name) in workflows[kind].inputs.items():
            node = workflows[kind].workflow.get(node_id)
            if node is None or input_name not in node.get("inputs", {}):
                raise SystemExit(
                    f"{kind} workflow: node {node_id!r} has no input {input_name!r} "
                    f"(for {field}). Run `inspect` on {wf_path.name} to find the right node."
                )
    return Config(
        server=raw.get("server", "http://127.0.0.1:8188").rstrip("/"),
        output_dir=(base / raw["output_dir"]).resolve(),
        workflows=workflows,
        timeout_seconds=float(raw.get("timeout_seconds", 900)),
        poll_seconds=float(raw.get("poll_seconds", 2)),
        attempts=int(raw.get("attempts", 2)),
    )


def apply_inputs(
    workflow: Dict[str, Any], mapping: Dict[str, List[str]], values: Dict[str, Any]
) -> Dict[str, Any]:
    """A copy of ``workflow`` with each mapped field set; unmapped values are ignored,
    and a value without a mapping is simply not applied."""
    result = copy.deepcopy(workflow)
    for field, value in values.items():
        if field in mapping and value is not None:
            node_id, input_name = mapping[field]
            result[node_id]["inputs"][input_name] = value
    return result


# --- ComfyUI HTTP API ----------------------------------------------------------------


class ComfyClient:
    def __init__(self, server: str, timeout: float = 60.0):
        self.server = server
        self.timeout = timeout
        self.client_id = uuid.uuid4().hex

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> bytes:
        request = urllib.request.Request(
            self.server + path, data=body, method=method, headers=headers or {}
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read()

    def system_stats(self) -> Dict[str, Any]:
        return json.loads(self._request("GET", "/system_stats"))

    def queue(self, workflow: Dict[str, Any]) -> str:
        body = json.dumps({"prompt": workflow, "client_id": self.client_id}).encode()
        try:
            data = self._request("POST", "/prompt", body, {"Content-Type": "application/json"})
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:2000]
            raise RuntimeError(f"ComfyUI rejected the workflow: {detail}") from exc
        return json.loads(data)["prompt_id"]

    def history(self, prompt_id: str) -> Optional[Dict[str, Any]]:
        data = json.loads(self._request("GET", f"/history/{prompt_id}"))
        return data.get(prompt_id)

    def download(self, image: Dict[str, str]) -> bytes:
        query = urllib.parse.urlencode(
            {
                "filename": image["filename"],
                "subfolder": image.get("subfolder", ""),
                "type": image.get("type", "output"),
            }
        )
        return self._request("GET", f"/view?{query}")

    def upload(self, data: bytes, filename: str) -> str:
        """Upload an input image; returns the name LoadImage expects."""
        boundary = uuid.uuid4().hex
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
            f'filename="{filename}"\r\nContent-Type: image/png\r\n\r\n'.encode(),
            data,
            f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\n'
            f"true\r\n--{boundary}--\r\n".encode(),
        ]
        body = b"".join(parts)
        result = json.loads(
            self._request(
                "POST",
                "/upload/image",
                body,
                {"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
        )
        return (
            f"{result['subfolder']}/{result['name']}" if result.get("subfolder") else result["name"]
        )


def wait_for_image(
    client: ComfyClient,
    prompt_id: str,
    output_node: Optional[str],
    timeout: float,
    poll: float,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        entry = client.history(prompt_id)
        if entry:
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                messages = [
                    m for m in status.get("messages", []) if m and m[0] == "execution_error"
                ]
                raise RuntimeError(f"ComfyUI execution error: {json.dumps(messages)[:1500]}")
            outputs = entry.get("outputs", {})
            nodes = [output_node] if output_node else list(outputs)
            for node_id in nodes:
                images = outputs.get(node_id, {}).get("images", [])
                if images:
                    return client.download(images[0])
            if status.get("completed"):
                raise RuntimeError("ComfyUI finished without producing an image")
        sleep(poll)
    raise TimeoutError(f"no image after {timeout:.0f} s")


# --- the run ---------------------------------------------------------------------------


def read_manifest(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def paths_for(output_dir: Path, job: Dict[str, Any]) -> Dict[str, Path]:
    folder = output_dir / job["item_id"]
    return {
        "image": folder / f"{job['image_id']}.png",
        "sidecar": folder / f"{job['image_id']}.json",
        "error": folder / f"{job['image_id']}.error.json",
    }


def is_done(output_dir: Path, job: Dict[str, Any]) -> bool:
    paths = paths_for(output_dir, job)
    if not (paths["image"].is_file() and paths["sidecar"].is_file()):
        return False
    try:
        sidecar = json.loads(paths["sidecar"].read_text())
    except json.JSONDecodeError:
        return False
    if sidecar.get("sha256") != hashlib.sha256(paths["image"].read_bytes()).hexdigest():
        return False
    # A changed job (new prompt, seed or size) makes the old image stale: redo it.
    return all(sidecar.get(k) == job.get(k) for k in ("prompt", "seed", "width", "height"))


def make_image(
    client: ComfyClient,
    config: Config,
    job: Dict[str, Any],
    sleep: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    workflow_config = config.workflows["generate" if job["kind"] == "generate" else "edit"]
    values: Dict[str, Any] = {
        "prompt": job["prompt"],
        "negative_prompt": job["negative_prompt"],
        "seed": job["seed"],
        "width": job["width"],
        "height": job["height"],
        "filename_prefix": f"helios/{job['item_id']}/{job['image_id']}",
    }
    if job["kind"] == "edit":
        source = paths_for(config.output_dir, {**job, "image_id": job["source_image_id"]})["image"]
        if not source.is_file():
            raise FileNotFoundError(f"source image {job['source_image_id']} has not been made yet")
        values["image"] = client.upload(source.read_bytes(), source.name)
    workflow = apply_inputs(workflow_config.workflow, workflow_config.inputs, values)
    started = time.monotonic()
    prompt_id = client.queue(workflow)
    data = wait_for_image(
        client,
        prompt_id,
        workflow_config.output_node,
        config.timeout_seconds,
        config.poll_seconds,
        sleep,
    )
    paths = paths_for(config.output_dir, job)
    paths["image"].parent.mkdir(parents=True, exist_ok=True)
    tmp = paths["image"].with_suffix(".png.tmp")
    tmp.write_bytes(data)
    tmp.replace(paths["image"])
    sidecar = {
        **job,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "workflow": workflow_config.name,
        "workflow_file": workflow_config.path.name,
        "workflow_sha256": workflow_config.sha256,
        "comfyui_prompt_id": prompt_id,
        "seconds": round(time.monotonic() - started, 2),
        "made_at": datetime.now(timezone.utc).isoformat(),
        "runner_version": RUNNER_VERSION,
    }
    paths["sidecar"].write_text(json.dumps(sidecar, indent=2, sort_keys=True))
    if paths["error"].exists():
        paths["error"].unlink()
    return sidecar


def run(
    client: ComfyClient,
    config: Config,
    jobs: List[Dict[str, Any]],
    limit: Optional[int] = None,
    views: Optional[List[str]] = None,
    log: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
) -> Dict[str, int]:
    counts = {"made": 0, "skipped": 0, "failed": 0}
    try:
        stats = client.system_stats()
        (config.output_dir).mkdir(parents=True, exist_ok=True)
        (config.output_dir / "server.json").write_text(json.dumps(stats, indent=2))
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(f"ComfyUI is not reachable at {config.server}: {exc}") from exc
    selected = [j for j in jobs if not views or j["view"] in views]
    for job in selected:
        if limit is not None and counts["made"] >= limit:
            break
        if is_done(config.output_dir, job):
            counts["skipped"] += 1
            continue
        for attempt in range(1, config.attempts + 1):
            try:
                sidecar = make_image(client, config, job, sleep)
                counts["made"] += 1
                log(f"made {job['image_id']} in {sidecar['seconds']} s")
                break
            except Exception as exc:  # report and move on; a later run retries it
                if attempt == config.attempts:
                    counts["failed"] += 1
                    error_path = paths_for(config.output_dir, job)["error"]
                    error_path.parent.mkdir(parents=True, exist_ok=True)
                    error_path.write_text(
                        json.dumps(
                            {
                                "image_id": job["image_id"],
                                "error": f"{type(exc).__name__}: {exc}",
                                "at": datetime.now(timezone.utc).isoformat(),
                            },
                            indent=2,
                        )
                    )
                    log(f"FAILED {job['image_id']}: {type(exc).__name__}: {exc}")
    return counts


def status(config: Config, jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
    done = [j for j in jobs if is_done(config.output_dir, j)]
    failed = [j for j in jobs if paths_for(config.output_dir, j)["error"].exists()]
    seconds = []
    for job in done:
        try:
            seconds.append(
                json.loads(paths_for(config.output_dir, job)["sidecar"].read_text())["seconds"]
            )
        except (KeyError, json.JSONDecodeError):
            pass
    average = sum(seconds) / len(seconds) if seconds else None
    remaining = len(jobs) - len(done)
    return {
        "jobs": len(jobs),
        "done": len(done),
        "failed": len(failed),
        "remaining": remaining,
        "average_seconds": round(average, 1) if average else None,
        "estimated_hours_left": round(remaining * average / 3600, 1) if average else None,
    }


def inspect(path: Path) -> None:
    workflow = json.loads(path.read_text())
    if "nodes" in workflow and "links" in workflow:
        raise SystemExit(
            f"{path.name} is a UI workflow; export it in API format instead (see README)."
        )
    for node_id, node in sorted(
        workflow.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0
    ):
        title = node.get("_meta", {}).get("title", "")
        inputs = {k: v for k, v in node.get("inputs", {}).items() if not isinstance(v, list)}
        class_type = node.get("class_type", "?")
        print(f"{node_id:>5}  {class_type:<32} {title:<28} {json.dumps(inputs)[:120]}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    inspect_cmd = commands.add_parser("inspect", help="list a workflow's nodes and inputs")
    inspect_cmd.add_argument("workflow", type=Path)
    for name in ("run", "status"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--manifest", type=Path, required=True)
        cmd.add_argument("--config", type=Path, required=True)
        if name == "run":
            cmd.add_argument("--limit", type=int, help="stop after making this many images")
            cmd.add_argument("--views", help="only these views, e.g. front or front,angle")
    args = parser.parse_args(argv)

    if args.command == "inspect":
        inspect(args.workflow)
        return 0
    config = load_config(args.config)
    jobs = read_manifest(args.manifest)
    if args.command == "status":
        print(json.dumps(status(config, jobs), indent=2))
        return 0
    views = [v.strip() for v in args.views.split(",")] if args.views else None
    counts = run(ComfyClient(config.server), config, jobs, args.limit, views)
    print(json.dumps({**counts, **status(config, jobs)}, indent=2))
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
