from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GKECluster:
    name: str
    project_id: str
    location: str
    location_type: str   # "zone" | "region"
    status: str
    num_nodes: int
    k8s_version: str
    selected: bool = False

    @property
    def row_key(self) -> str:
        return f"{self.project_id}/{self.location}/{self.name}"

    @property
    def credentials_cmd(self) -> list[str]:
        flag = "--region" if self.location_type == "region" else "--zone"
        return [
            "gcloud", "container", "clusters", "get-credentials",
            self.name, flag, self.location, f"--project={self.project_id}",
        ]
