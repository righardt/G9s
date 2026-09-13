# g9s — Requirements

A k9s-styled terminal UI for browsing GCP resources and merging GKE cluster
credentials into a single local kubeconfig. Built with Python + [Textual](https://textual.textualize.io/).

## 1. Purpose

Let a user interactively:

1. Browse the GCP projects they're authorized against.
2. Drill into a project and browse its resources (GKE clusters, Compute
   instances, Cloud SQL instances, Storage buckets).
3. Pick one or more GKE clusters — across any number of projects — and merge
   their credentials into a single `~/.kube/config`, so every selected
   cluster is available on the system `kubectl` PATH.

All GCP-facing operations are **read-only** (`list`/`describe`/`get-credentials`).
The only writes are local: temp credential files and `~/.kube/config` itself
(backed up first).

## 2. Distribution

- Installable Python package (not a single script), built with **uv** +
  hatchling — `uv sync`, `uv run g9s`, `uv tool install .`, `uv build`.
- `pyproject.toml` defines a console-script entry point: `g9s = "g9s.__main__:main"`.
- Project name / package / CLI command / app class are all **`g9s`** (renamed
  from an earlier "k9m" working name — the app is explicitly modeled on k9s,
  hence the name).

## 3. Navigation model

A k9s-style drill-down stack, not a single flat table:

```
Projects  →  Resource Kinds (per project)  →  Resource List (per kind)  →  Detail
```

- **Projects** — `gcloud projects list` (no filter; every lifecycle state is
  returned, not just `ACTIVE`).
- **Resource Kinds** — a static, no-gcloud-call menu of resource categories
  available for the selected project. Currently: GKE Clusters, Compute
  Instances, Cloud SQL Instances, Storage Buckets. Adding a new kind is a
  registry entry (`g9s/resources.py`), not a new screen.
- **Resource List** — one `list` call for the chosen kind/project
  (`gcloud container clusters list`, `gcloud compute instances list`,
  `gcloud sql instances list`, `gcloud storage buckets list`).
- **Detail** — one `describe` call for the selected row, rendered as
  formatted JSON.

Rules:

- Exactly **one gcloud call per screen**, fired only the first time that
  screen is pushed.
- Popping back (`<esc>`) to an already-visited screen reuses its cached rows
  — it does **not** re-fetch.
- Organizations/Folders are explicitly **out of scope for now** — the stack
  starts at Projects. (`gcloud resource-manager folders list` has no
  parent-less "list everything" form anyway, so Folders can only ever be
  reached by drilling into a specific Org later, if that's added.)

## 4. GKE cluster selection & kubeconfig generation

- Only the **GKE Clusters** resource kind is selectable (checkbox column).
- Selections are held at the app level (not per-screen), so picking clusters
  in one project, navigating elsewhere, and picking more clusters in a
  different project all accumulate into one set.
- `<g>` opens a modal that, for each selected cluster: runs
  `gcloud container clusters get-credentials` into a temp file, then merges
  everything with `kubectl config view --merge --flatten` into
  `~/.kube/config` (existing file backed up to `~/.kube/config.bak` first).

## 5. Filtering & sorting

- `/` opens a live filter bar on the current list screen — substring match
  (case-insensitive) across every rendered column. `<enter>` keeps the filter
  applied and closes the bar; `<esc>` clears the filter and closes the bar.
  Works on every list screen (Projects, Resource Kinds, any Resource List),
  not just GKE Clusters.
- `<a>` / `<A>` (select all / deselect all) act on the currently *visible*
  (filtered) rows only.
- Clicking any column header sorts the table by that column (click again to
  reverse). Numeric-looking columns (e.g. node count) sort numerically, not
  lexically.
- The Projects screen has a **STATUS** column showing `lifecycleState`
  (`ACTIVE` / `DELETE_REQUESTED` / `DELETE_IN_PROGRESS`, colour-coded).

## 6. Command bar

A trimmed-down k9s-style `:` command bar (not a full clone — no fuzzy
suggestions/history):

- `:projects` (or `:proj`) — reset the stack back to the Projects root.
- `:gke`, `:compute`, `:sql`, `:storage` (plus aliases like `:clusters`,
  `:vm`, `:cloudsql`, `:bucket`) — jump straight to that resource kind's list
  for whichever project is currently in context, skipping the Resource Kinds
  menu.

## 7. Layout — k9s visual style

No sidebar. Header is a three-column row:

- **Left — info block** (white text, gold labels):
  ```
  Organization:  n/a [RW]
  User    :  <active gcloud auth account>
  Selected:  <N> cluster(s)          — GKE clusters queued for kubeconfig generation
  Project :  <current project in nav stack, or "—" at root>
  g9s Rev :  v<version>
  Status  :  <last operation / loading state>
  ```
- **Centre — key hints**, blue key / dim description pairs, horizontally
  centered between the info block and the logo (not hugging either side).
- **Right — ASCII logo**, gold, bold, right-aligned, positioned at the top of
  the header (no top padding).

Below the header: a bordered, full-width table (or detail view) with an
embedded title showing a breadcrumb (e.g. `proj-a / GKE Clusters[7]`,
`selected:2`, active filter text when set). Below that, a bottom tab-pill
strip showing the current resource tag (e.g. `<gke>`, `<projects>`).

Color palette: dark background (`#0b0c16`), gold accents (`#f5a623`), blue
key hints (`#4da6ff`), k9s-style green/yellow/red status coloring.

## 8. Command log panel

`<l>` toggles a bordered log panel showing every underlying `gcloud`/`kubectl`
command that's actually been run, and its result (success/exit code, or a
truncated error snippet). Every subprocess call in `gcloud.py` is routed
through this so nothing runs invisibly.

## 9. Key bindings (current)

| Key | Action |
|---|---|
| `↑`/`k`, `↓`/`j` | Move cursor |
| `enter` | Drill into the current row |
| `esc` | Back up one level (or close the command/filter bar if open) |
| `:` | Open command bar |
| `/` | Open filter bar (live substring filter on current screen) |
| `space` | Toggle current row (GKE Clusters screen only) |
| `a` / `A` | Select all / deselect all visible rows (GKE Clusters screen only) |
| `g` | Generate merged kubeconfig from all selected clusters |
| `l` | Toggle command log panel |
| `r` | Refresh current screen (bypasses cache, re-fetches) |
| `?` | Help screen |
| `q` | Quit |

## 10. Non-goals (explicitly out of scope for now)

- Organizations / Folders levels of the resource hierarchy.
- Any GCP-mutating operation beyond local credential files and
  `~/.kube/config`.
- Full k9s command-bar parity (fuzzy suggestions, command history,
  `xray`/`alias`/etc. special verbs).
- Editing/creating/deleting any browsed resource — every resource kind is
  list + describe only.
