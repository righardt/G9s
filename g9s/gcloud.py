"""gcloud / kubectl subprocess helpers.  All functions are blocking and
must be called from worker threads, never from the async event loop."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from .models import GKECluster


# ── Command log callback ───────────────────────────────────────────────────────
# Set once at startup by the app; called from worker threads to stream every
# command + result line to the in-app log panel.

_log_fn: Callable[[str], None] | None = None


def set_log_callback(fn: Callable[[str], None]) -> None:
    global _log_fn
    _log_fn = fn


def _emit(msg: str) -> None:
    if _log_fn:
        _log_fn(msg)


# ──────────────────────────────────────────────────────────────────────────────

def _run(
    cmd: list[str],
    env: dict | None = None,
    timeout: int = 30,
) -> tuple[bool, str, str]:
    _emit(f"[dim]$[/] [cyan]{' '.join(cmd)}[/]")
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env or os.environ.copy(),
        )
        if r.returncode == 0:
            _emit(f"  [green]✓[/] [dim]exit 0[/]")
        else:
            snippet = (r.stderr.strip() or r.stdout.strip())[:120]
            _emit(f"  [red]✗ exit {r.returncode}[/]  [dim]{snippet}[/]")
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        _emit("  [red]✗ timed out[/]")
        return False, "", "timed out"
    except FileNotFoundError:
        _emit(f"  [red]✗ command not found: {cmd[0]}[/]")
        return False, "", f"command not found: {cmd[0]}"


def fetch_projects() -> list[dict]:
    ok, out, err = _run(
        ["gcloud", "projects", "list", "--format=json"],
        timeout=60,
    )
    if not ok:
        raise RuntimeError(err or "gcloud projects list failed")
    return json.loads(out) if out else []


def fetch_clusters(project_id: str) -> list[dict]:
    ok, out, _ = _run(
        ["gcloud", "container", "clusters", "list",
         f"--project={project_id}", "--format=json"],
        timeout=30,
    )
    if not ok or not out:
        return []
    return json.loads(out) or []


def location_type(location: str) -> str:
    """Return 'zone' or 'region'.

    Zones end with a single letter after the last dash (e.g. africa-south1-a).
    Regions don't (e.g. africa-south1, us-central1).
    """
    last = location.rsplit("-", 1)[-1]
    return "zone" if (len(last) == 1 and last.isalpha()) else "region"


def describe_cluster(
    project_id: str, name: str, location: str, location_type: str,
) -> dict:
    flag = "--region" if location_type == "region" else "--zone"
    ok, out, err = _run(
        ["gcloud", "container", "clusters", "describe", name,
         flag, location, f"--project={project_id}", "--format=json"],
        timeout=30,
    )
    if not ok:
        raise RuntimeError(err or "gcloud container clusters describe failed")
    return json.loads(out) if out else {}


def fetch_compute_instances(project_id: str) -> list[dict]:
    ok, out, _ = _run(
        ["gcloud", "compute", "instances", "list",
         f"--project={project_id}", "--format=json"],
        timeout=30,
    )
    if not ok or not out:
        return []
    return json.loads(out) or []


def describe_compute_instance(project_id: str, name: str, zone: str) -> dict:
    ok, out, err = _run(
        ["gcloud", "compute", "instances", "describe", name,
         f"--project={project_id}", f"--zone={zone}", "--format=json"],
        timeout=30,
    )
    if not ok:
        raise RuntimeError(err or "gcloud compute instances describe failed")
    return json.loads(out) if out else {}


def fetch_sql_instances(project_id: str) -> list[dict]:
    ok, out, _ = _run(
        ["gcloud", "sql", "instances", "list",
         f"--project={project_id}", "--format=json"],
        timeout=30,
    )
    if not ok or not out:
        return []
    return json.loads(out) or []


def describe_sql_instance(project_id: str, name: str) -> dict:
    ok, out, err = _run(
        ["gcloud", "sql", "instances", "describe", name,
         f"--project={project_id}", "--format=json"],
        timeout=30,
    )
    if not ok:
        raise RuntimeError(err or "gcloud sql instances describe failed")
    return json.loads(out) if out else {}


def fetch_storage_buckets(project_id: str) -> list[dict]:
    ok, out, _ = _run(
        ["gcloud", "storage", "buckets", "list",
         f"--project={project_id}", "--format=json"],
        timeout=30,
    )
    if not ok or not out:
        return []
    return json.loads(out) or []


def describe_bucket(name: str) -> dict:
    ok, out, err = _run(
        ["gcloud", "storage", "buckets", "describe", f"gs://{name}",
         "--format=json"],
        timeout=30,
    )
    if not ok:
        raise RuntimeError(err or "gcloud storage buckets describe failed")
    return json.loads(out) if out else {}


def fetch_active_account() -> str:
    ok, out, _ = _run(
        ["gcloud", "auth", "list", "--filter=status:ACTIVE",
         "--format=value(account)"],
        timeout=15,
    )
    if not ok or not out:
        return ""
    return out.splitlines()[0].strip()


def is_logged_in() -> bool:
    """Return True if at least one active gcloud account exists."""
    return bool(fetch_active_account())


def run_login(line_callback: Callable[[str], None] | None = None) -> tuple[bool, str]:
    """Run `gcloud auth login` (browser-based), streaming each output line to
    `line_callback` as it arrives so the caller can show progress in real time
    while the user authenticates in the browser.
    Returns (success, error_message).
    """
    cmd = ["gcloud", "auth", "login"]
    _emit(f"[dim]$[/] [cyan]{' '.join(cmd)}[/]")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        lines: list[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            stripped = line.rstrip()
            lines.append(stripped)
            if line_callback:
                line_callback(stripped)
        proc.wait(timeout=300)
        if proc.returncode == 0:
            _emit("  [green]✓ login successful[/]")
            return True, ""
        err = "\n".join(lines[-5:])
        _emit(f"  [red]✗ login failed (exit {proc.returncode})[/]")
        return False, err
    except subprocess.TimeoutExpired:
        _emit("  [red]✗ timed out[/]")
        return False, "gcloud auth login timed out after 5 minutes"
    except FileNotFoundError:
        _emit("  [red]✗ gcloud not found[/]")
        return False, "gcloud not found on PATH"


def rename_context(old_name: str, new_name: str, kubeconfig: str) -> tuple[bool, str]:
    """Rename a context inside a kubeconfig file in-place."""
    ok, _, err = _run(
        ["kubectl", "config", "rename-context", old_name, new_name,
         "--kubeconfig", kubeconfig],
        timeout=10,
    )
    return ok, err


def get_credentials_to_file(cluster: GKECluster, path: str) -> tuple[bool, str]:
    env = {**os.environ, "KUBECONFIG": path}
    ok, _, err = _run(cluster.credentials_cmd, env=env, timeout=45)
    return ok, err


def read_kubeconfig_gke_keys(path: str) -> set[tuple[str, str, str, str]]:
    """Parse an existing kubeconfig and return a set of
    (project_id, location, cluster_name, context_name) 4-tuples.

    Parses GKE identity from the context's `cluster` reference
    (context.context.cluster), NOT the context name — this correctly
    handles contexts that have been renamed from their default GKE name.
    Falls back to parsing the context name itself if the cluster ref
    is not GKE-formatted.  Non-parseable contexts get empty fields.
    """
    config_path = Path(path)
    if not config_path.exists():
        return set()
    try:
        r = subprocess.run(
            ["kubectl", "config", "view",
             "--kubeconfig", str(config_path),
             "--output=json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return set()
        cfg = json.loads(r.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError,
            json.JSONDecodeError, OSError):
        return set()
    result: set[tuple[str, str, str, str]] = set()
    for ctx in cfg.get("contexts") or []:
        ctx_name: str = ctx.get("name") or ""
        if not ctx_name:
            continue
        cluster_ref: str = (ctx.get("context") or {}).get("cluster") or ""
        project_id, location, cluster_name = "", "", ""
        for candidate in (cluster_ref, ctx_name):
            parts = candidate.split("_", 3)
            if len(parts) == 4 and parts[0] == "gke":
                _, project_id, location, cluster_name = parts
                break
        if not cluster_name:
            cluster_name = ctx_name   # last resort
        result.add((project_id, location, cluster_name, ctx_name))
    return result


def merge_kubeconfigs(files: list[str], output: str) -> tuple[bool, str]:
    """Merge kubeconfig files using kubectl config view --merge --flatten."""
    env = {**os.environ, "KUBECONFIG": ":".join(files)}
    ok, out, err = _run(
        ["kubectl", "config", "view", "--merge", "--flatten"],
        env=env,
        timeout=15,
    )
    if not ok:
        return False, err or "kubectl merge failed"
    try:
        Path(output).write_text(out)
        os.chmod(output, 0o600)
        return True, ""
    except OSError as exc:
        return False, str(exc)
