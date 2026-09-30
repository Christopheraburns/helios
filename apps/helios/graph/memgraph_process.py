"""Supervises the Memgraph server process inside a Workbench Application pod.

Memgraph speaks Bolt, which a Workbench Application cannot expose -- the ingress
proxy only forwards HTTP on CDSW_APP_PORT. So Memgraph is bound to loopback and
reached only by the gateway running in this same pod.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

BOLT_HOST = "127.0.0.1"
BOLT_PORT = int(os.environ.get("HELIOS_GRAPH_BOLT_PORT", "7687"))

# Candidate install locations, in the order the Memgraph packages use.
_BINARY_CANDIDATES = (
    "/usr/lib/memgraph/memgraph",
    "/usr/bin/memgraph",
    "/opt/memgraph/memgraph",
)

# Fraction of the pod's memory ceiling handed to Memgraph; the rest covers
# uvicorn, the driver and the parser that O-2 will add.
_MEMORY_SHARE = 0.6
_MIN_MEMORY_MIB = 256


def binary_path() -> str:
    """Locate the memgraph executable, honouring HELIOS_GRAPH_MEMGRAPH_BIN."""
    override = os.environ.get("HELIOS_GRAPH_MEMGRAPH_BIN")
    if override:
        if not os.access(override, os.X_OK):
            raise RuntimeError(f"HELIOS_GRAPH_MEMGRAPH_BIN is not executable: {override}")
        return override
    for candidate in _BINARY_CANDIDATES:
        if os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which("memgraph")
    if found:
        return found
    raise RuntimeError(
        "memgraph binary not found in "
        f"{', '.join(_BINARY_CANDIDATES)} or on PATH; "
        "set HELIOS_GRAPH_MEMGRAPH_BIN or use the helios-graph runtime"
    )


def pod_memory_limit_mib() -> int | None:
    """Read the container's memory ceiling in MiB, or None when unlimited."""
    sources = (
        Path("/sys/fs/cgroup/memory.max"),  # cgroup v2
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),  # cgroup v1
    )
    for source in sources:
        try:
            raw = source.read_text().strip()
        except OSError:
            continue
        if raw == "max":
            return None
        try:
            limit = int(raw)
        except ValueError:
            continue
        # cgroup v1 reports a sentinel near 2^63 when no limit is set.
        if limit <= 0 or limit >= 1 << 62:
            return None
        return limit // (1024 * 1024)
    return None


def memgraph_memory_limit_mib() -> int:
    """Memory ceiling to hand Memgraph, as its --memory-limit flag (MiB)."""
    override = os.environ.get("HELIOS_GRAPH_MEMORY_LIMIT_MIB")
    if override:
        return max(_MIN_MEMORY_MIB, int(override))
    pod_limit = pod_memory_limit_mib()
    if pod_limit is None:
        return 0  # Memgraph treats 0 as "no limit".
    return max(_MIN_MEMORY_MIB, int(pod_limit * _MEMORY_SHARE))


def data_directory() -> Path:
    """Where Memgraph keeps its durability files.

    Node-local by default: the graph is a disposable copy rebuilt from the
    lakehouse, and project storage is NFS, which is a poor fit for a database.
    """
    configured = os.environ.get("HELIOS_GRAPH_DATA_DIR")
    base = Path(configured) if configured else Path("/tmp/helios-graph/memgraph")
    base.mkdir(parents=True, exist_ok=True)
    return base


def bolt_is_listening(host: str = BOLT_HOST, port: int = BOLT_PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


class MemgraphProcess:
    """Starts and stops Memgraph as a child of the gateway process."""

    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None
        self.command: list[str] = []

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    def start(self) -> None:
        if self.running:
            return
        self.command = [
            binary_path(),
            f"--bolt-address={BOLT_HOST}",
            f"--bolt-port={BOLT_PORT}",
            f"--data-directory={data_directory()}",
            f"--memory-limit={memgraph_memory_limit_mib()}",
            "--log-level=WARNING",
            "--also-log-to-stderr",
            "--telemetry-enabled=false",
        ]
        print("starting memgraph:", " ".join(self.command), flush=True)
        self._process = subprocess.Popen(self.command)

    def wait_until_listening(self, timeout: float = 60.0) -> None:
        """Block until Bolt accepts connections, or raise."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise RuntimeError(
                    f"memgraph exited during startup with code {self._process.returncode}"
                )
            if bolt_is_listening():
                return
            time.sleep(0.25)
        raise TimeoutError(f"memgraph did not accept Bolt connections within {timeout:.0f}s")

    def stop(self, timeout: float = 15.0) -> None:
        if not self.running:
            self._process = None
            return
        assert self._process is not None
        self._process.terminate()
        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=timeout)
        self._process = None
