# G9s

A [K9s](https://k9scli.io/)-inspired terminal UI To Managing kubeconfig from GCP.
```
  ________________       
 /  _____/   __   \______
/   \  __\____    /  ___/
\    \_\  \ /    /\___ \ 
 \______  //____//____  >
        \/            \/ 
```

G9s reads your existing `~/.kube/config`, lets you browse GCP resources, add or remove clusters, rename contexts, and merge everything into a single kubeconfig with one keypress.


---

## Features

- **Reads your existing kubeconfig** on startup — configured clusters are shown immediately, pre-selected, and ready to regenerate.
- **Drill-down browser** — Projects → Resource Kinds (GKE, Compute, SQL, Storage) → Resource List → Detail JSON view.
- **Multi-project selection** — select GKE clusters across any number of projects; selections persist as you navigate.
- **Context rename** — give any cluster a custom context name before generating the merged config.
- **One-command generation** — `<g>` fetches credentials for every selected cluster and writes a merged `~/.kube/config`.
- **K9s-style UX** — command bar (`:`), live filter (`/`), vim keys (`j`/`k`), column sort, command log panel.
- **Correctly handles renamed contexts** — uses `context.cluster` (not the context name) to resolve GKE cluster identity, so renamed contexts like `poephol` are matched to the right project/location/cluster.

---

## Prerequisites

| Tool | Purpose |
|---|---|
| Python ≥ 3.11 | Runtime |
| [uv](https://docs.astral.sh/uv/) | Package manager |
| [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) (`gcloud`) | List projects, clusters, fetch credentials |
| `kubectl` | Merge kubeconfig files, rename contexts |

---

## Installation

### From source (recommended during development)

```bash
git clone https://github.com/righardt/g9s.git
cd g9s
uv sync
uv run g9s
```

### Install as a tool

```bash
uv tool install .
g9s
```

### Build a wheel

```bash
uv build
```

---

## Usage

### Startup

1. **Auth check** — if no active gcloud account is found, G9s offers to run `gcloud auth login` (opens browser).
2. **Workspace prompt** — confirm or change the kubeconfig directory (default `~/.kube`). The existing `config` file in that directory is scanned for GKE contexts.
3. **Home screen** — if existing GKE clusters are found they appear pre-selected on the `:configured` screen. If not, a prompt asks whether to navigate to Projects instead.

### Key bindings

| Key | Action |
|---|---|
| `↑` / `k` | Move up |
| `↓` / `j` | Move down |
| `enter` | Drill into row (or rename context on `:configured`) |
| `esc` | Go back / close bar |
| `:` | Open command bar |
| `/` | Live filter (current screen) |
| `space` | Toggle cluster selection |
| `a` / `A` | Select all / deselect all visible |
| `d` | Remove cluster from `:configured` screen |
| `g` | Generate merged kubeconfig |
| `w` | Change workspace directory |
| `l` | Toggle command log |
| `p` | Toggle preselect panel |
| `r` | Refresh current screen |
| `?` | Help |
| `q` | Quit |

### Command bar (`:`)

| Command | Action |
|---|---|
| `:configured` / `:home` | Jump to Configured Clusters screen |
| `:projects` | Jump to Projects screen |
| `:gke` | Jump to GKE Clusters for current project |
| `:compute` | Jump to Compute Instances |
| `:sql` | Jump to Cloud SQL Instances |
| `:storage` | Jump to Storage Buckets |

### Typical workflow

```
Launch G9s
  → Confirm workspace (Enter)
  → See your existing clusters pre-selected on :configured
  → Navigate to :projects to add more clusters:
      Select a project → Enter → GKE Clusters → Enter
      Space to select, Esc to go back
  → Return to :configured to see the full list
  → (Optional) Enter on a row to rename its context
  → Press g to generate ~/.kube/config
```

---

## Project structure

```
g9s/
├── g9s/
│   ├── __init__.py      # version
│   ├── __main__.py      # entry point
│   ├── app.py           # TUI app, screens, nav stack
│   ├── gcloud.py        # gcloud/kubectl subprocess helpers
│   ├── models.py        # GKECluster dataclass
│   └── resources.py     # resource kind registry (GKE, Compute, SQL, Storage)
├── pyproject.toml
├── uv.lock
├── requirement.md       # full requirements specification
└── README.md
```

---

## Adding a new resource kind

Edit `g9s/resources.py` — add a `ResourceKind` entry to `RESOURCE_KINDS` with the list/describe functions and column definitions. No new screen class needed.

---

## License

MIT
