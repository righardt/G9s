# G9s — Requirements

A K9s-styled terminal UI for browsing GCP resources and managing GKE cluster
credentials into a single local kubeconfig. Built with Python + [Textual](https://textual.textualize.io/).

---

## 1. Purpose

Let a user interactively:

1. Start from an existing `~/.kube/config` (or a user-specified workspace folder) and see which clusters are already configured.
2. Browse GCP projects and drill into their resources (GKE clusters, Compute instances, Cloud SQL instances, Storage buckets).
3. Pick additional GKE clusters — across any number of projects — and merge all selected credentials into a single `~/.kube/config`, so every selected cluster is available on the system `kubectl` PATH.
4. Manage context names (rename contexts before generation) and remove clusters from the configured list.

All GCP-facing operations are **read-only** (`list`/`describe`/`get-credentials`). The only writes are local: temp credential files, `~/.kube/config` itself (backed up first to `~/.kube/config.bak`), and in-place context renames inside temp files.

---

## 2. Startup flow

On launch, G9s runs through a fixed sequence:

1. **Auth check** — runs `gcloud auth list` in the background. If no active account is found, a modal offers to run `gcloud auth login` (browser-based) or quit.
2. **Workspace selection** — prompts for the kubeconfig workspace directory (default `~/.kube`). The user can change the path, confirm, or quit.
3. **Kubeconfig scan** — the existing `config` file in the workspace is read. `context.cluster` is inspected for every context entry (not just the context name) so renamed GKE contexts are correctly resolved to their underlying project/location/cluster.
4. **Home screen**:
   - If the kubeconfig has GKE contexts: land on the **Configured Clusters** screen with all found clusters pre-selected.
   - If not: show a modal explaining no config was found, with options to proceed to Projects or quit.

---

## 3. Distribution

- Installable Python package, built with **uv** + hatchling.
- Entry point: `g9s = "g9s.__main__:main"` — run with `uv run g9s` or `g9s` after install.
- Requires Python ≥ 3.11, `textual ≥ 0.47.0`.
- Prerequisites on PATH: `gcloud` (Google Cloud SDK) and `kubectl`.

---

## 4. Configured Clusters screen (`:configured`)

The home screen when an existing kubeconfig is found. Reachable anytime via `:configured`, `:config`, or `:home`.

- Shows every context from the existing kubeconfig with columns: `CONTEXT`, `CLUSTER`, `PROJECT`, `LOCATION`.
  - **CONTEXT** — the context name as stored in the kubeconfig (may be a custom/renamed name).
  - **CLUSTER** — the short GKE cluster resource name, resolved from `context.cluster` in the kubeconfig, not from the context name. This correctly handles contexts that have been renamed from their default `gke_{project}_{location}_{cluster}` form.
  - **PROJECT** / **LOCATION** — also resolved from `context.cluster`.
- All contexts are pre-selected (`✓`) on load.
- Additionally-selected clusters (added via Projects drill-down) appear here too so the full set is always visible in one place.
- **`enter`** on a row opens a **Rename Context** modal — the user can edit the context name that will be written to the merged kubeconfig. The field defaults to the original context name (or `gke_{project}_{location}_{cluster}` if no name is available).
- **`d`** removes a cluster from this screen: it is deselected and removed from the kubeconfig context list so it won't be included in the next generation.
- **`space` / `a` / `A`** toggle individual / all / none of the visible rows.

---

## 5. Navigation model

A K9s-style lazy drill-down stack. Each screen makes exactly one `gcloud` call, fired only on first load. Popping back with `<esc>` reuses cached rows.

```
Configured Clusters (home)
      │
      ├── esc → stays at home
      │
Projects  →  Resource Kinds  →  Resource List  →  Detail (JSON)
```

- **Projects** — `gcloud projects list` (all lifecycle states). Has a **STATUS** column (`ACTIVE` / `DELETE_REQUESTED` / `DELETE_IN_PROGRESS`, colour-coded).
- **Resource Kinds** — static menu per project: GKE Clusters, Compute Instances, Cloud SQL Instances, Storage Buckets. No gcloud call.
- **Resource List** — one `list` call per kind/project. All columns are sortable (click header; click again to reverse). Numeric columns sort numerically.
- **Detail** — one `describe` call, rendered as formatted JSON.

Adding a new browsable resource kind requires only a registry entry in `g9s/resources.py` — no new screen class.

---

## 6. GKE cluster selection & kubeconfig generation

- Only **GKE Clusters** resource kind is selectable.
- Selections accumulate at the app level across all screens and projects.
- **`<g>`** opens the Generate modal which, for each selected cluster:
  1. Runs `gcloud container clusters get-credentials` into a temp file.
  2. If the user gave the cluster a custom context name, runs `kubectl config rename-context OLD NEW` on the temp file.
  3. Merges all temp files with `kubectl config view --merge --flatten` into `{workspace}/config`.
- Every gcloud/kubectl command is visible in the Command Log panel (`<l>`).

---

## 7. Filtering

- **`/`** opens a live filter bar (K9s-style, with a fixed `/ ` prefix that cannot be deleted) — substring match across every rendered column, case-insensitive.
- `<enter>` keeps the filter and closes the bar; `<esc>` clears the filter and closes the bar.
- Works on every list screen including Projects, Configured Clusters, and all Resource Lists.
- `<a>` / `<A>` (select all / deselect all) act on the currently *visible* (filtered) rows only.
- The breadcrumb shows `[visible/total]` and the active filter text while a filter is applied.

---

## 8. Command bar

A K9s-style `:` command bar (fixed `> ` prefix that cannot be deleted):

| Command | Action |
|---|---|
| `:configured`, `:config`, `:home` | Jump to Configured Clusters screen |
| `:projects`, `:proj` | Jump to Projects screen |
| `:gke`, `:clusters`, `:cluster` | Jump to GKE Clusters list for current project |
| `:compute`, `:instances`, `:vm`, `:vms` | Jump to Compute Instances list |
| `:sql`, `:cloudsql` | Jump to Cloud SQL Instances list |
| `:storage`, `:bucket`, `:buckets`, `:gcs` | Jump to Storage Buckets list |

---

## 9. Panels

- **`<l>` Command Log** — blue-bordered panel showing every `gcloud`/`kubectl` command run and its result (exit code or truncated error). Includes pre-selection matching debug output when clusters are loaded.
- **`<p>` Preselect** — green-bordered panel showing the contexts read from the existing kubeconfig file at startup, with their resolved cluster/project/location.

---

## 10. Layout — K9s visual style

No sidebar. Three-column header:

| Section | Content |
|---|---|
| Left (50 cols) | Info block with gold labels, white bold values: Organization, User, Selected (cluster count), Project, Workspace, G9s Rev, Status |
| Centre (`1fr`) | Key hints grid — K9s-style, up to 6 rows × N columns to fill available width. Blue keys, grey descriptions. |
| Right (30 cols) | ASCII logo, gold, right-aligned |

Below header: bordered table or detail view with breadcrumb title. Bottom: tab-pill strip.

Color palette matches K9s default dark skin:
- Background: terminal default (`$background`)
- Borders: dodgerblue (`#1e90ff`), table border: `#4a90d9`
- Filter prompt border: seagreen (`#2e8b57`)
- Command prompt border: aqua (`#00cdcd`)
- Table cursor: aqua bg / black fg
- Logo / gold accents: `#f5a623`
- Key hint keys: dodgerblue (`#1e90ff`), descriptions: grey (`#8b949e`)
- Info block labels: orange (`#f5a623`), values: bold white

---

## 11. Key bindings

| Key | Action |
|---|---|
| `↑` / `k`, `↓` / `j` | Move cursor |
| `enter` | Drill into row (or rename context on Configured Clusters) |
| `esc` | Back up one level / close command or filter bar |
| `:` | Open command bar |
| `/` | Open filter bar |
| `space` | Toggle cluster selection (GKE / Configured screens) |
| `a` / `A` | Select all / deselect all visible rows |
| `d` | Delete cluster from Configured Clusters screen |
| `g` | Generate merged kubeconfig |
| `w` | Re-open workspace selection modal |
| `l` | Toggle command log panel |
| `p` | Toggle preselect panel |
| `r` | Refresh current screen |
| `?` | Help screen |
| `q` | Quit |

---

## 12. Non-goals (explicitly out of scope for now)

- Organizations / Folders levels of the GCP resource hierarchy.
- Any GCP-mutating operation (all `gcloud` calls are read-only against GCP).
- Full K9s command-bar parity (fuzzy suggestions, command history, special verbs).
- Editing, creating, or deleting any GCP resource — all resource kinds are list + describe only.
- Supporting non-GKE kubeconfig clusters for generation (non-GKE contexts are shown and tracked but cannot have credentials re-fetched via gcloud).
