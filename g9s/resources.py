"""Registry of browsable GCP resource kinds.

Adding a new browsable resource type is a matter of adding an entry here plus
the list/describe functions in gcloud.py — no new screen classes needed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import gcloud
from .models import GKECluster


@dataclass(frozen=True)
class ResourceKind:
    key: str
    label: str
    aliases: tuple[str, ...]
    # (header, dotted-field-path, optional value formatter)
    columns: tuple[tuple[str, str, Callable[[str], str] | None], ...]
    list_fn: Callable[[str], list[dict]]
    row_key_fn: Callable[[dict], str]
    describe_fn: Callable[[str, dict], dict] | None = None
    selectable: bool = False


def field_value(row: dict, path: str) -> str:
    cur = row
    for part in path.split("."):
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(part)
    return "" if cur is None else str(cur)


def _short(value: str) -> str:
    """Shorten a GCE self-link URL to its last path segment."""
    return value.rsplit("/", 1)[-1] if value else value


def gke_cluster_from_row(project_id: str, row: dict) -> GKECluster:
    loc = row.get("location") or row.get("zone", "")
    ver = row.get("currentMasterVersion") or row.get("masterVersion") or "?"
    return GKECluster(
        name=row.get("name", ""),
        project_id=project_id,
        location=loc,
        location_type=gcloud.location_type(loc),
        status=row.get("status", "UNKNOWN"),
        num_nodes=int(row.get("currentNodeCount", 0) or 0),
        k8s_version=str(ver)[:12],
    )


def _gke_row_key(row: dict) -> str:
    return f"{row.get('name', '')}/{row.get('location', '') or row.get('zone', '')}"


def _gke_describe(project_id: str, row: dict) -> dict:
    location = row.get("location") or row.get("zone", "")
    location_type = gcloud.location_type(location)
    return gcloud.describe_cluster(project_id, row.get("name", ""), location, location_type)


def _compute_row_key(row: dict) -> str:
    return f"{row.get('name', '')}/{_short(row.get('zone', ''))}"


def _compute_describe(project_id: str, row: dict) -> dict:
    zone = _short(row.get("zone", ""))
    return gcloud.describe_compute_instance(project_id, row.get("name", ""), zone)


def _sql_row_key(row: dict) -> str:
    return row.get("name", "")


def _sql_describe(project_id: str, row: dict) -> dict:
    return gcloud.describe_sql_instance(project_id, row.get("name", ""))


def _storage_row_key(row: dict) -> str:
    return row.get("name", row.get("id", ""))


def _storage_describe(project_id: str, row: dict) -> dict:
    return gcloud.describe_bucket(row.get("name", row.get("id", "")))


RESOURCE_KINDS: list[ResourceKind] = [
    ResourceKind(
        key="gke",
        label="GKE Clusters",
        aliases=("gke", "clusters", "cluster"),
        columns=(
            ("CLUSTER", "name", None),
            ("LOCATION", "location", None),
            ("NODES", "currentNodeCount", None),
            ("K8S VER", "currentMasterVersion", None),
            ("STATUS", "status", None),
        ),
        list_fn=gcloud.fetch_clusters,
        row_key_fn=_gke_row_key,
        describe_fn=_gke_describe,
        selectable=True,
    ),
    ResourceKind(
        key="compute",
        label="Compute Instances",
        aliases=("compute", "instances", "vm", "vms"),
        columns=(
            ("NAME", "name", None),
            ("ZONE", "zone", _short),
            ("MACHINE TYPE", "machineType", _short),
            ("STATUS", "status", None),
        ),
        list_fn=gcloud.fetch_compute_instances,
        row_key_fn=_compute_row_key,
        describe_fn=_compute_describe,
    ),
    ResourceKind(
        key="sql",
        label="Cloud SQL Instances",
        aliases=("sql", "cloudsql"),
        columns=(
            ("NAME", "name", None),
            ("DATABASE VERSION", "databaseVersion", None),
            ("REGION", "region", None),
            ("STATE", "state", None),
        ),
        list_fn=gcloud.fetch_sql_instances,
        row_key_fn=_sql_row_key,
        describe_fn=_sql_describe,
    ),
    ResourceKind(
        key="storage",
        label="Storage Buckets",
        aliases=("storage", "bucket", "buckets", "gcs"),
        columns=(
            ("NAME", "name", None),
            ("LOCATION", "location", None),
            ("STORAGE CLASS", "storageClass", None),
        ),
        list_fn=gcloud.fetch_storage_buckets,
        row_key_fn=_storage_row_key,
        describe_fn=_storage_describe,
    ),
]


def resolve_alias(text: str) -> ResourceKind | None:
    text = text.strip().lower()
    for kind in RESOURCE_KINDS:
        if text == kind.key or text in kind.aliases:
            return kind
    return None
