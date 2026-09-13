"""g9s TUI — screens and main application."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from textual import work, on, events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import DataTable, Static, Button, RichLog, Input
from rich.text import Text

from .gcloud import (
    fetch_active_account,
    fetch_projects,
    get_credentials_to_file,
    is_logged_in,
    merge_kubeconfigs,
    read_kubeconfig_gke_keys,
    rename_context,
    run_login,
    set_log_callback,
)
from .models import GKECluster
from .resources import (
    RESOURCE_KINDS,
    ResourceKind,
    field_value,
    gke_cluster_from_row,
    resolve_alias,
)
from . import __version__


# ──────────────────────────────────────────────────────────────────────────────
# ASCII logo — rendered in gold (#f5a623) to match the info-block colour.
# ──────────────────────────────────────────────────────────────────────────────

LOGO = (
r"""  ________________       
 /  _____/   __   \______
/   \  __\____    /  ___/
\    \_\  \ /    /\___ \ 
 \______  //____//____  >
        \/            \/ 

"""
)

# ──────────────────────────────────────────────────────────────────────────────
# Help screen
# ──────────────────────────────────────────────────────────────────────────────

HELP_TEXT = """\
[bold #f5a623]⎈  G9s  —  GCP / GKE resource browser[/]

[bold]Navigation[/]
  [#f5a623]↑  k[/]       Move up            [#f5a623]↓  j[/]    Move down
  [#f5a623]enter[/]      Drill into row      [#f5a623]esc[/]    Back up
  [#f5a623]:[/]          Command bar (e.g. [dim]:gke[/], [dim]:sql[/], [dim]:projects[/], [dim]:configured[/])
  [#f5a623]/[/]          Filter current list (live, substring match across all columns)

[bold]Selection[/] [dim](GKE Clusters screen only)[/]
  [#f5a623]<space>[/]  Toggle current cluster
  [#f5a623]a[/]        Select all clusters
  [#f5a623]A[/]        Deselect all clusters

[bold]Actions[/]
  [#f5a623]g[/]        Generate combined kubeconfig from selected clusters → [dim]~/.kube/config[/]
  [#f5a623]l[/]        Toggle command log panel
  [#f5a623]r[/]        Refresh current screen
  [#f5a623]?[/]        This help screen
  [#f5a623]q[/]        Quit

[bold]Output[/]
  Writes to [#f5a623]~/.kube/config[/].
  The existing file is backed up to [#f5a623]~/.kube/config.bak[/]
  before each generation.

[bold]Prerequisites[/]
  [dim]gcloud auth login[/]
  [dim]gcloud auth application-default login[/]
"""


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape,q,?", "dismiss", "Close", show=False)]

    def compose(self) -> ComposeResult:
        yield Container(
            Static(HELP_TEXT, markup=True, id="help-body"),
            Button("Close  Esc", id="btn-help-close"),
            id="help-modal",
        )

    @on(Button.Pressed, "#btn-help-close")
    def _close(self) -> None:
        self.dismiss()


# ──────────────────────────────────────────────────────────────────────────────
# Generate screen
# ──────────────────────────────────────────────────────────────────────────────

class LoginScreen(ModalScreen[bool]):
    """Shown when no active gcloud account is detected.
    Yes → runs `gcloud auth login --no-launch-browser` and streams output.
    No  → dismisses with False so the app can exit gracefully.
    """

    BINDINGS = []

    def compose(self) -> ComposeResult:
        yield Container(
            Static(
                "[bold #f5a623]⚠  Not logged in to gcloud[/]\n\n"
                "No active gcloud account was found.\n"
                "G9s needs [bold]gcloud auth login[/] to list projects and clusters.\n\n"
                "Run [bold]gcloud auth login[/] now? Your browser will open for authentication.",
                markup=True,
                id="login-title",
            ),
            RichLog(id="login-log", markup=True, highlight=True),
            Horizontal(
                Button("Login", id="btn-login-yes"),
                Button("Quit", id="btn-login-no"),
                id="login-buttons",
            ),
            id="login-modal",
        )

    @on(Button.Pressed, "#btn-login-yes")
    def _do_login(self) -> None:
        self.query_one("#btn-login-yes", Button).disabled = True
        self.query_one("#btn-login-no", Button).disabled = True
        self._run_login()

    @on(Button.Pressed, "#btn-login-no")
    def _skip(self) -> None:
        self.dismiss(False)

    @work(thread=True)
    def _run_login(self) -> None:
        log = self.query_one("#login-log", RichLog)
        app = self.app

        def emit(msg: str) -> None:
            app.call_from_thread(log.write, msg)

        emit("[dim]Running:[/] [cyan]gcloud auth login[/]  [dim](browser will open)[/]")
        ok, err = run_login(line_callback=emit)
        if ok:
            emit("[green]✓ Login successful — continuing…[/]")
            app.call_from_thread(self.dismiss, True)
        else:
            emit(f"[red]✗ Login failed:[/] {err}")
            app.call_from_thread(
                self.query_one("#btn-login-no", Button).__setattr__, "disabled", False
            )
            app.call_from_thread(
                self.query_one("#btn-login-no", Button).__setattr__, "label", "Close"
            )


class WorkspaceScreen(ModalScreen[str]):
    """Ask the user to confirm (or change) the kubeconfig output directory."""

    BINDINGS = [Binding("escape", "dismiss_default", show=False)]

    def __init__(self, detected: str) -> None:
        super().__init__()
        self._detected = detected

    def compose(self) -> ComposeResult:
        yield Container(
            Static(
                "[bold #f5a623]Kubeconfig workspace[/]\n\n"
                f"G9s will read & write [bold]config[/] (and [bold]config.bak[/]) to/from:\n",
                markup=True,
                id="ws-title",
            ),
            Input(value=self._detected, id="ws-input"),
            Static(
                "\n[dim]Edit the path above if needed, then press [bold]Enter[/] to confirm.[/]",
                markup=True,
                id="ws-hint",
            ),
            Horizontal(
                Button("Confirm", id="btn-ws-confirm"),
                Button("Quit", id="btn-ws-quit"),
                id="ws-buttons",
            ),
            id="ws-modal",
        )

    def on_mount(self) -> None:
        self.query_one("#ws-input", Input).focus()

    def action_dismiss_default(self) -> None:
        self.dismiss(self._detected)

    @on(Input.Submitted, "#ws-input")
    def _submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or self._detected)

    @on(Button.Pressed, "#btn-ws-confirm")
    def _confirm(self) -> None:
        val = self.query_one("#ws-input", Input).value.strip()
        self.dismiss(val or self._detected)

    @on(Button.Pressed, "#btn-ws-quit")
    def _quit(self) -> None:
        self.app.exit()


class GenerateScreen(ModalScreen[bool]):
    BINDINGS = []

    def __init__(self, clusters: list[GKECluster], workspace: str,
                 cluster_names: dict[str, str] | None = None) -> None:
        super().__init__()
        self._clusters = clusters
        self._workspace = workspace
        self._cluster_names: dict[str, str] = cluster_names or {}

    def compose(self) -> ComposeResult:
        n = len(self._clusters)
        yield Container(
            Static(
                f"[bold #f5a623]Generating kubeconfig for {n} cluster(s)…[/]",
                id="gen-title",
            ),
            RichLog(id="gen-log", markup=True, highlight=True),
            Button("Close", id="btn-gen-close", disabled=True),
            id="gen-modal",
        )

    def on_mount(self) -> None:
        self._run()

    @work(thread=True)
    def _run(self) -> None:
        log = self.query_one("#gen-log", RichLog)

        app = self.app

        def emit(msg: str) -> None:
            app.call_from_thread(log.write, msg)

        tmp = tempfile.mkdtemp(prefix="g9s_")
        collected: list[str] = []

        for i, c in enumerate(self._clusters, 1):
            emit(
                f"[dim]({i}/{len(self._clusters)})[/]  "
                f"[#f5a623]{c.project_id}[/] / [bold]{c.name}[/]"
                f"  [dim]{c.location}[/]"
            )
            out_file = os.path.join(tmp, f"ctx_{i}.yaml")
            ok, err = get_credentials_to_file(c, out_file)
            p = Path(out_file)
            if ok and p.exists() and p.stat().st_size:
                # Rename context if the user gave it a custom name.
                default_name = f"gke_{c.project_id}_{c.location}_{c.name}"
                sel_key = f"{c.project_id}:gke:{c.name}/{c.location}"
                custom_name = self._cluster_names.get(sel_key, default_name)
                if custom_name != default_name:
                    ok_r, err_r = rename_context(default_name, custom_name, out_file)
                    if ok_r:
                        emit(f"  [cyan]↻ renamed → [bold]{custom_name}[/][/]")
                    else:
                        emit(f"  [yellow]! rename failed: {err_r}[/]")
                collected.append(out_file)
                emit("  [green]✓ credentials fetched[/]")
            else:
                emit(f"  [red]✗ {err or 'no output'}[/]")

        if not collected:
            emit("\n[red bold]No credentials retrieved — nothing written.[/]")
            app.call_from_thread(self._done)
            return

        kube_dir = Path(self._workspace).expanduser()
        kube_dir.mkdir(parents=True, exist_ok=True)
        out_path = str(kube_dir / "config")
        bak_path = str(kube_dir / "config.bak")

        if Path(out_path).exists():
            emit(f"\nBacking up [dim]{out_path}[/] → [dim]{bak_path}[/]")
            try:
                shutil.copy2(out_path, bak_path)
                emit("  [green]✓ backed up[/]")
            except OSError as exc:
                emit(f"  [yellow]! {exc}[/]")

        emit("\nMerging all contexts…")
        ok, err = merge_kubeconfigs(collected, out_path)
        if ok:
            emit(f"  [green]✓ written → {out_path}[/]")
            emit(
                f"\n[bold green]Done![/]  "
                f"{len(collected)} context(s) now available via [#f5a623]kubectl[/]"
            )
        else:
            emit(f"  [red]✗ {err}[/]")

        app.call_from_thread(self._done)

    def _done(self) -> None:
        self.query_one("#btn-gen-close", Button).disabled = False

    @on(Button.Pressed, "#btn-gen-close")
    def _close(self) -> None:
        self.dismiss(True)


# ──────────────────────────────────────────────────────────────────────────────
# Navigation stack — every screen is a "frame". Exactly one gcloud call fires
# per frame, the first time it's pushed; popping back to an already-visited
# frame reuses its cached rows instead of re-fetching.
# ──────────────────────────────────────────────────────────────────────────────

class RenameContextModal(ModalScreen[str | None]):
    """Edit the kubeconfig context name for a configured cluster."""

    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(self, current_name: str) -> None:
        super().__init__()
        self._current = current_name

    def compose(self) -> ComposeResult:
        yield Container(
            Static(
                "[bold #f5a623]Edit context name[/]\n\n"
                "This name will be used in the merged kubeconfig file.\n"
                "Convention: [dim]gke_{project}_{location}_{cluster}[/]",
                markup=True,
                id="rename-title",
            ),
            Input(value=self._current, id="rename-input"),
            Horizontal(
                Button("Confirm", id="btn-rename-confirm"),
                Button("Cancel", id="btn-rename-cancel"),
                id="rename-buttons",
            ),
            id="rename-modal",
        )

    def on_mount(self) -> None:
        inp = self.query_one("#rename-input", Input)
        inp.focus()
        inp.cursor_position = len(inp.value)

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted, "#rename-input")
    def _submit(self, event: Input.Submitted) -> None:
        val = event.value.strip()
        self.dismiss(val if val else None)

    @on(Button.Pressed, "#btn-rename-confirm")
    def _confirm(self) -> None:
        val = self.query_one("#rename-input", Input).value.strip()
        self.dismiss(val if val else None)

    @on(Button.Pressed, "#btn-rename-cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class NoConfigModal(ModalScreen[bool]):
    """Shown when no existing kubeconfig / GKE contexts are found."""

    BINDINGS = []

    def compose(self) -> ComposeResult:
        yield Container(
            Static(
                "[bold #f5a623]⚠  No existing kubeconfig found[/]\n\n"
                f"No GKE contexts were found in your workspace config file.\n"
                "G9s can navigate to the Projects list so you can select\n"
                "and download cluster credentials for the first time.",
                markup=True,
                id="noconfig-title",
            ),
            Horizontal(
                Button("Proceed", id="btn-noconfig-proceed"),
                Button("Quit", id="btn-noconfig-quit"),
                id="noconfig-buttons",
            ),
            id="noconfig-modal",
        )

    @on(Button.Pressed, "#btn-noconfig-proceed")
    def _proceed(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#btn-noconfig-quit")
    def _quit(self) -> None:
        self.dismiss(False)


class NavFrame:
    rows: list[dict] | None = None
    detail_text: str | None = None
    filter_text: str = ""
    visible: list[dict] | None = None  # rows actually shown after filtering

    def is_detail(self) -> bool:
        return False

    def selectable(self) -> bool:
        return False

    def title(self) -> str:
        raise NotImplementedError

    def tab_label(self) -> str:
        return self.__class__.__name__.replace("Frame", "").lower()

    def columns(self) -> list[str]:
        raise NotImplementedError

    def row_values(self, app: "G9S", row: dict) -> list:
        raise NotImplementedError

    def row_key(self, row: dict) -> str:
        raise NotImplementedError

    def load(self, app: "G9S") -> None:
        raise NotImplementedError

    def on_enter(self, app: "G9S", row: dict) -> "NavFrame | None":
        return None

    def sort_key(self, idx: int, app: "G9S"):
        def key(row):
            v = self.row_values(app, row)[idx]
            return (v.plain if isinstance(v, Text) else str(v)).lower()
        return key

    def matches_filter(self, app: "G9S", row: dict) -> bool:
        if not self.filter_text:
            return True
        needle = self.filter_text.lower()
        haystack = " ".join(
            v.plain if isinstance(v, Text) else str(v)
            for v in self.row_values(app, row)
        ).lower()
        return needle in haystack


_PROJECT_STATUS_STYLES: dict[str, str] = {
    "ACTIVE":            "green",
    "DELETE_REQUESTED":  "yellow",
    "DELETE_IN_PROGRESS": "red",
}


class ProjectsFrame(NavFrame):
    def title(self) -> str:
        return "projects"

    def tab_label(self) -> str:
        return "projects"

    def columns(self) -> list[str]:
        return ["PROJECT ID", "NAME", "STATUS"]

    def row_values(self, app: "G9S", row: dict) -> list:
        status = row.get("lifecycleState", "UNKNOWN")
        style = _PROJECT_STATUS_STYLES.get(status, "dim")
        return [row.get("projectId", ""), row.get("name", ""), Text(status, style=style)]

    def row_key(self, row: dict) -> str:
        return row.get("projectId", "")

    def load(self, app: "G9S") -> None:
        try:
            self.rows = fetch_projects()
        except RuntimeError as exc:
            app.call_from_thread(app._load_error, str(exc))
            return
        app.call_from_thread(app._render_frame, self)

    def on_enter(self, app: "G9S", row: dict) -> "NavFrame | None":
        return ResourceKindsFrame(row.get("projectId", ""))


class ResourceKindsFrame(NavFrame):
    def __init__(self, project_id: str) -> None:
        self.project_id = project_id

    def title(self) -> str:
        return f"{self.project_id} / resources"

    def tab_label(self) -> str:
        return "resources"

    def columns(self) -> list[str]:
        return ["RESOURCE TYPE"]

    def row_values(self, app: "G9S", row: dict) -> list:
        return [row["label"]]

    def row_key(self, row: dict) -> str:
        return row["key"]

    def load(self, app: "G9S") -> None:
        # Static menu — no gcloud call.
        self.rows = [{"key": k.key, "label": k.label} for k in RESOURCE_KINDS]
        app.call_from_thread(app._render_frame, self)

    def on_enter(self, app: "G9S", row: dict) -> "NavFrame | None":
        kind = next((k for k in RESOURCE_KINDS if k.key == row["key"]), None)
        return ResourceListFrame(self.project_id, kind) if kind else None


class ResourceListFrame(NavFrame):
    def __init__(self, project_id: str, kind: ResourceKind) -> None:
        self.project_id = project_id
        self.kind = kind

    def title(self) -> str:
        return f"{self.project_id} / {self.kind.label}"

    def tab_label(self) -> str:
        return self.kind.key

    def selectable(self) -> bool:
        return self.kind.selectable

    def columns(self) -> list[str]:
        heads = [h for h, _, _ in self.kind.columns]
        return ["", *heads] if self.kind.selectable else heads

    def row_values(self, app: "G9S", row: dict) -> list:
        vals: list = []
        for header, path, fmt in self.kind.columns:
            v = field_value(row, path)
            if fmt:
                v = fmt(v)
            if header == "STATUS":
                v = Text(v, style=app.STATUS_STYLES.get(v, "dim"))
            vals.append(v)
        if self.kind.selectable:
            key = app.sel_key(self, row)
            mark = (
                Text("✓", style="bold green")
                if key in app._selected
                else Text("·", style="#333355")
            )
            vals = [mark, *vals]
        return vals

    def row_key(self, row: dict) -> str:
        return self.kind.row_key_fn(row)

    def load(self, app: "G9S") -> None:
        try:
            self.rows = self.kind.list_fn(self.project_id)
        except RuntimeError as exc:
            app.call_from_thread(app._load_error, str(exc))
            return
        app.call_from_thread(app._render_frame, self)

    def on_enter(self, app: "G9S", row: dict) -> "NavFrame | None":
        if self.kind.describe_fn is None:
            return None
        return DetailFrame(self.project_id, self.kind, row)

    def sort_key(self, idx: int, app: "G9S"):
        offset = 1 if self.kind.selectable else 0
        if self.kind.selectable and idx == 0:
            return lambda row: app.sel_key(self, row) not in app._selected
        _, path, fmt = self.kind.columns[idx - offset]

        def key(row):
            v = field_value(row, path)
            if fmt:
                v = fmt(v)
            try:
                return (0, float(v))
            except ValueError:
                return (1, v.lower())
        return key


class DetailFrame(NavFrame):
    def __init__(self, project_id: str, kind: ResourceKind, row: dict) -> None:
        self.project_id = project_id
        self.kind = kind
        self.row = row

    def is_detail(self) -> bool:
        return True

    def title(self) -> str:
        return f"{self.project_id} / {self.kind.label} / {self.kind.row_key_fn(self.row)}"

    def tab_label(self) -> str:
        return "detail"

    def load(self, app: "G9S") -> None:
        try:
            data = self.kind.describe_fn(self.project_id, self.row)
        except RuntimeError as exc:
            app.call_from_thread(app._load_error, str(exc))
            return
        self.detail_text = json.dumps(data, indent=2, default=str)
        app.call_from_thread(app._render_frame, self)


class ConfiguredClustersFrame(NavFrame):
    """Home screen when an existing kubeconfig is found.

    Shows all GKE contexts from the kubeconfig file without a gcloud call —
    the user can see/manage what's already configured, then navigate to
    Projects to add more.
    """

    def __init__(self, contexts: list[tuple[str, str, str]]) -> None:
        self._contexts = contexts  # [(project_id, location, cluster_name), ...]

    def title(self) -> str:
        return "configured clusters"

    def tab_label(self) -> str:
        return "configured"

    def selectable(self) -> bool:
        return True

    def columns(self) -> list[str]:
        return ["", "CONTEXT", "CLUSTER", "PROJECT", "LOCATION"]

    def _row_for(self, ctx: tuple[str, str, str, str]) -> dict:
        project_id, location, cluster_name, context_name = ctx
        return {
            "name": cluster_name, "location": location,
            "_project_id": project_id, "_context_name": context_name,
        }

    def _sel_key(self, project_id: str, cluster_name: str, location: str,
                 context_name: str = "") -> str:
        return f"{project_id}:gke:{cluster_name}/{location}"

    @staticmethod
    def default_context_name(context_name: str, project_id: str,
                             location: str, cluster_name: str) -> str:
        return context_name or (
            f"gke_{project_id}_{location}_{cluster_name}" if project_id else cluster_name
        )

    def row_values(self, app: "G9S", row: dict) -> list:
        proj = row["_project_id"]
        name = row["name"]
        loc = row["location"]
        ctx_name = row.get("_context_name", "")
        key = self._sel_key(proj, name, loc)
        display_ctx = app._cluster_names.get(
            key, self.default_context_name(ctx_name, proj, loc, name)
        )
        mark = (
            Text("✓", style="bold green") if key in app._selected
            else Text("·", style="#333355")
        )
        return [mark, display_ctx, name, proj or "[dim]—[/]", loc or "[dim]—[/]"]

    def row_key(self, row: dict) -> str:
        return self._sel_key(row["_project_id"], row["name"], row["location"])

    def load(self, app: "G9S") -> None:
        seen: set[str] = set()
        rows: list[dict] = []
        for proj, loc, cluster, ctx_name in self._contexts:
            key = self._sel_key(proj, cluster, loc)
            if key not in seen:
                seen.add(key)
                rows.append({"name": cluster, "location": loc,
                             "_project_id": proj, "_context_name": ctx_name})
        for cluster_obj in list(app._selected.values()):
            key = self._sel_key(cluster_obj.project_id, cluster_obj.name, cluster_obj.location)
            if key not in seen:
                seen.add(key)
                rows.append({
                    "name": cluster_obj.name,
                    "location": cluster_obj.location,
                    "_project_id": cluster_obj.project_id,
                    "_context_name": "",
                })
        self.rows = rows
        app.call_from_thread(app._render_frame, self)

    def on_enter(self, app: "G9S", row: dict) -> "NavFrame | None":
        return None  # Enter is handled in _on_row_selected to show rename modal

    def sort_key(self, idx: int, app: "G9S"):
        fields = ["", "_ctx_name", "_project_id", "name", "location"]
        field = fields[idx] if idx < len(fields) else ""

        def key(row):
            if idx == 0:
                return self._sel_key(row["_project_id"], row["name"], row["location"]) not in app._selected
            return row.get(field, "").lower()
        return key


# ──────────────────────────────────────────────────────────────────────────────
# PromptBar — k9s-style prompt widget with a fixed, undeletable prefix
# ──────────────────────────────────────────────────────────────────────────────

class PromptBar(Widget, can_focus=True):
    """Replicates k9s's tview.TextView-based prompt: fixed coloured prefix,
    user text rendered after it, block cursor, manual key capture."""

    class Changed(Message):
        def __init__(self, bar: "PromptBar", value: str) -> None:
            super().__init__()
            self.bar = bar
            self.value = value

        @property
        def control(self) -> "PromptBar":
            return self.bar

    class Submitted(Message):
        def __init__(self, bar: "PromptBar", value: str) -> None:
            super().__init__()
            self.bar = bar
            self.value = value

        @property
        def control(self) -> "PromptBar":
            return self.bar

    def __init__(self, prefix: str, prefix_color: str, border_color: str, widget_id: str) -> None:
        super().__init__(id=widget_id)
        self._prefix = prefix
        self._prefix_color = prefix_color
        self._border_color = border_color
        self._text: str = ""

    def render(self) -> Text:
        t = Text(no_wrap=True, overflow="crop")
        t.append(self._prefix, style=f"bold {self._prefix_color}")
        t.append(self._text, style="bold white")
        t.append("█", style=f"{self._border_color}")
        return t

    def on_key(self, event: events.Key) -> None:
        if event.key == "backspace":
            if self._text:
                self._text = self._text[:-1]
                self._notify_changed()
            event.stop()
        elif event.key == "ctrl+w":
            self._text = self._text.rsplit(" ", 1)[0] if " " in self._text else ""
            self._notify_changed()
            event.stop()
        elif event.key == "enter":
            self.post_message(self.Submitted(self, self._text))
            event.stop()
        elif event.key == "escape":
            pass  # let it bubble to action_nav_back
        elif event.character and event.character.isprintable():
            self._text += event.character
            self._notify_changed()
            event.stop()

    def _notify_changed(self) -> None:
        self.refresh()
        self.post_message(self.Changed(self, self._text))

    def clear(self) -> None:
        self._text = ""
        self.refresh()

    @property
    def value(self) -> str:
        return self._text

    @value.setter
    def value(self, v: str) -> None:
        self._text = v
        self.refresh()


# ──────────────────────────────────────────────────────────────────────────────
# CSS — k9s colour palette
# ──────────────────────────────────────────────────────────────────────────────

APP_CSS = """\
/* ── Palette (mirrors k9s default dark skin) ──────────────────────
   black=#000000  aqua=#00cdcd  dodgerblue=#1e90ff  orange=#f5a623
   seagreen=#2e8b57  cadetblue=#5f9ea0  white=#ffffff
   ──────────────────────────────────────────────────────────────── */

Screen {
    background: $background;
}

/* ─── Header ─────────────────────────────────────────────────────── */
#header {
    height: 7;
    background: $background;
}

#header-info {
    width: 50;
    padding: 0 1;
    color: #ffffff;
}

#header-keys {
    width: 1fr;
    padding: 0 1;
    color: #5f9ea0;
    align: left top;
}

#key-hints {
    text-align: left;
    width: 1fr;
}

#header-logo {
    width: 30;
    padding: 0;
    color: #f5a623;
    text-style: bold;
    text-align: right;
}

/* ─── Filter bar (/) — seagreen border like k9s ──────────────────── */
#filter-bar {
    display: none;
    height: 3;
    background: $background;
    border: solid #2e8b57;
    padding: 0 1;
    color: #5f9ea0;
}
#filter-bar:focus {
    border: solid #2e8b57;
}

/* ─── Command bar (:) — aqua border like k9s ────────────────────── */
#cmd-bar {
    display: none;
    height: 3;
    background: $background;
    border: solid #00cdcd;
    padding: 0 1;
    color: #5f9ea0;
}
#cmd-bar:focus {
    border: solid #00cdcd;
}

/* ─── Table / detail area ────────────────────────────────────────── */
#table-wrap {
    height: 1fr;
    background: $background;
    border: solid #4a90d9;
    border-title-align: center;
    border-title-color: #00cdcd;
    border-title-style: bold;
}

DataTable {
    background: $background;
    color: #5f9ea0;
    scrollbar-color: #1e90ff;
    scrollbar-color-hover: #f5a623;
}

DataTable > .datatable--header {
    background: $background;
    color: #ffffff;
    text-style: bold;
}

DataTable > .datatable--cursor {
    background: #00cdcd;
    color: #000000;
    text-style: bold;
}

DataTable > .datatable--hover {
    background: #0d1117;
}

#detail-view {
    display: none;
    background: $background;
    scrollbar-color: #1e90ff;
    scrollbar-color-hover: #f5a623;
    color: #5f9ea0;
}

/* ─── Command log panel ──────────────────────────────────────────── */
#cmd-panel {
    height: 12;
    background: $background;
    border: solid #1e90ff;
    border-title-color: #f5a623;
    border-title-style: bold;
    display: none;
}

#cmd-log {
    background: $background;
    scrollbar-color: #1e90ff;
    scrollbar-color-hover: #f5a623;
    color: #5f9ea0;
}

/* ─── Preselect panel ────────────────────────────────────────────── */
#preselect-panel {
    height: 12;
    background: $background;
    border: solid #2e8b57;
    border-title-color: #f5a623;
    border-title-style: bold;
    display: none;
}

#preselect-log {
    background: $background;
    scrollbar-color: #2e8b57;
    scrollbar-color-hover: #f5a623;
    color: #5f9ea0;
}

/* ─── Tab strip ──────────────────────────────────────────────────── */
#tab-strip {
    height: 1;
    background: $background;
    border-top: solid #1e90ff;
}

.tab-pill {
    background: #f5a623;
    color: #000000;
    text-style: bold;
    width: auto;
    padding: 0 1;
}

.tab-spacer {
    width: 1fr;
    background: $background;
}

/* ─── Modals ─────────────────────────────────────────────────────── */
ModalScreen {
    align: center middle;
    background: #00000088;
}

#help-modal {
    background: $background;
    border: solid #1e90ff;
    width: 64;
    height: auto;
    max-height: 90vh;
    padding: 2 3;
}
#help-body {
    margin-bottom: 1;
    padding-bottom: 1;
    border-bottom: solid #1e90ff;
}

#gen-modal {
    background: $background;
    border: solid #1e90ff;
    width: 78;
    height: 36;
    padding: 2 3;
}
#gen-title {
    text-align: center;
    padding-bottom: 1;
    border-bottom: solid #1e90ff;
    margin-bottom: 1;
}
#gen-log {
    height: 1fr;
    background: $background;
    border: solid #1e90ff;
    margin-bottom: 1;
    color: #5f9ea0;
}

/* ─── Rename context modal ──────────────────────────────────────── */
#rename-modal {
    background: $background;
    border: solid #1e90ff;
    width: 72;
    height: auto;
    padding: 2 3;
}
#rename-title {
    padding-bottom: 1;
    border-bottom: solid #1e90ff;
    margin-bottom: 1;
}
#rename-input {
    background: $background;
    border: solid #2e8b57;
    color: #ffffff;
    margin-bottom: 1;
}
#rename-input:focus { border: solid #2e8b57; }
#rename-buttons {
    height: 3;
    align: left middle;
}
#btn-rename-confirm, #btn-rename-cancel {
    width: 10;
    margin: 0 1;
}

/* ─── No-config modal ───────────────────────────────────────────── */
#noconfig-modal {
    background: $background;
    border: solid #f5a623;
    width: 64;
    height: auto;
    padding: 2 3;
}
#noconfig-title {
    padding-bottom: 1;
    border-bottom: solid #1e90ff;
    margin-bottom: 1;
}
#noconfig-buttons {
    height: 3;
    align: left middle;
}
#btn-noconfig-proceed, #btn-noconfig-quit {
    width: 10;
    margin: 0 1;
}

/* ─── Login modal ───────────────────────────────────────────────── */
#login-modal {
    background: $background;
    border: solid #f5a623;
    width: 72;
    height: auto;
    padding: 2 3;
}
#login-title {
    padding-bottom: 1;
    border-bottom: solid #1e90ff;
    margin-bottom: 1;
}
#login-log {
    height: 6;
    border: solid #1e90ff;
    margin-bottom: 1;
}
#login-buttons {
    height: 3;
    align: left middle;
}
#btn-login-yes, #btn-login-no {
    width: 10;
    margin: 0 1;
}

/* ─── Workspace modal ────────────────────────────────────────────── */
#ws-modal {
    background: $background;
    border: solid #1e90ff;
    width: 72;
    height: auto;
    padding: 2 3;
}
#ws-title {
    padding-bottom: 1;
    color: #5f9ea0;
}
#ws-hint {
    padding-top: 0;
    color: #5f9ea0;
}
#ws-input {
    background: $background;
    border: solid #2e8b57;
    color: #ffffff;
    margin-bottom: 1;
}
#ws-input:focus {
    border: solid #2e8b57;
}
#ws-buttons {
    height: 3;
    align: left middle;
}
#btn-ws-confirm, #btn-ws-quit {
    width: 10;
    margin: 0 1;
}

/* ─── Buttons ────────────────────────────────────────────────────── */
Button {
    background: #f5a623;
    color: #000000;
    text-style: bold;
    border: none;
    margin-top: 1;
}
Button:hover  { background: #ffc04d; }
Button:disabled {
    background: #1e90ff;
    color: #5f9ea0;
}
"""


# ──────────────────────────────────────────────────────────────────────────────
# App
# ──────────────────────────────────────────────────────────────────────────────

class G9S(App):
    TITLE = "G9s"
    CSS = APP_CSS

    STATUS_STYLES: dict[str, str] = {
        "RUNNING":     "green",
        "DEGRADED":    "yellow",
        "ERROR":       "bold red",
        "STOPPING":    "red",
        "RUNNABLE":    "green",
        "TERMINATED":  "dim",
    }

    BINDINGS = [
        Binding("space",         "toggle_sel",   show=False),
        Binding("a",             "select_all",   show=False),
        Binding("A",             "deselect_all", show=False),
        Binding("w",             "workspace",    show=False),
        Binding("g",             "generate",     show=False),
        Binding("l",             "toggle_log",      show=False),
        Binding("p",             "toggle_preselect", show=False),
        Binding("r",             "refresh",      show=False),
        Binding("question_mark", "help_modal",   show=False),
        Binding("d",             "delete_configured", show=False),
        Binding("q",             "quit",         show=False),
        Binding("j",             "move_down",    show=False),
        Binding("k",             "move_up",      show=False),
        Binding("escape",        "nav_back",     show=False),
        Binding("colon",         "open_cmdbar",  show=False),
        Binding("slash",         "open_filter",  show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._nav: list[NavFrame] = [ProjectsFrame()]
        self._selected: dict[str, GKECluster] = {}
        self._organization: str = "n/a"
        self._account: str = "n/a"
        self._status: str = "Initialising…"
        self._log_visible: bool = False
        self._sort_col: tuple[int, int] | None = None
        self._sort_reverse: bool = False
        self._workspace: str = str(Path.home() / ".kube")
        self._preselect: set[tuple[str, str, str]] = set()  # 3-tuple for GKE matching
        self._preselect_visible: bool = False
        self._kubeconfig_contexts: list[tuple[str, str, str, str]] = []  # 4-tuple incl. context_name
        self._cluster_names: dict[str, str] = {}  # sel_key → custom context name

    # ── Compose ──────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Horizontal(
            Vertical(Static("", id="info-block"), id="header-info"),
            Vertical(Static(self._key_hints(), id="key-hints", markup=True), id="header-keys"),
            Static(Text(LOGO, no_wrap=True, overflow="crop"), id="header-logo"),
            id="header",
        )
        yield PromptBar("🐩/ ", "#2e8b57", "#2e8b57", "filter-bar")
        yield PromptBar("🐶> ", "#00cdcd", "#00cdcd", "cmd-bar")
        with Container(id="table-wrap"):
            yield DataTable(cursor_type="row", zebra_stripes=False, id="nav-table")
            yield RichLog(id="detail-view", markup=True, highlight=True)
        with Container(id="cmd-panel"):
            yield RichLog(id="cmd-log", markup=True, highlight=True)
        with Container(id="preselect-panel"):
            yield RichLog(id="preselect-log", markup=True, highlight=True)
        yield Horizontal(
            Static("", id="tab-clusters", classes="tab-pill"),
            Static("", classes="tab-spacer"),
            id="tab-strip",
        )

    # ── Mount ────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self.query_one("#cmd-panel").border_title = " COMMAND LOG "
        self.query_one("#preselect-panel").border_title = " PRESELECT CONFIG "
        self._refresh_header()
        set_log_callback(self._log_cmd)
        self._activate_top()
        self.query_one("#nav-table", DataTable).focus()
        self.run_worker(self._check_auth, thread=True)

    def _check_auth(self) -> None:
        if is_logged_in():
            self.call_from_thread(self._post_auth)
        else:
            self.call_from_thread(self._show_login_prompt)

    def _show_login_prompt(self) -> None:
        def _after_login(logged_in: bool) -> None:
            if logged_in:
                self._post_auth()
            else:
                self.exit()
        self.push_screen(LoginScreen(), _after_login)

    def _post_auth(self) -> None:
        self.run_worker(self._load_account, thread=True)
        self._prompt_workspace()

    def _prompt_workspace(self) -> None:
        detected = str(Path.home() / ".kube")
        def _apply(path: str | None) -> None:
            if path:
                self._workspace = path.strip() or detected
            # Read existing kubeconfig to determine the home screen.
            config_path = str(Path(self._workspace) / "config")
            raw = read_kubeconfig_gke_keys(config_path)  # set of 4-tuples
            self._kubeconfig_contexts = sorted(raw)
            # _preselect holds 3-tuples for matching against GKE cluster lists
            self._preselect = {(p, l, c) for p, l, c, _ in raw if p}
            self._status = "Initialising…"

            if self._kubeconfig_contexts:
                home = ConfiguredClustersFrame(self._kubeconfig_contexts)
                self._nav = [home]
                for project_id, location, cluster_name, ctx_name in self._kubeconfig_contexts:
                    key = home._sel_key(project_id, cluster_name, location, ctx_name)
                    if key not in self._selected:
                        self._selected[key] = GKECluster(
                            name=cluster_name,
                            project_id=project_id,
                            location=location,
                            location_type="region" if not (
                                location.rsplit("-", 1)[-1].isalpha()
                                and len(location.rsplit("-", 1)[-1]) == 1
                            ) else "zone",
                            status="CONFIGURED",
                            num_nodes=0,
                            k8s_version="?",
                        )
                self._preselect = set()  # already applied
                self._activate_top()
                self._refresh_header()
            else:
                # No config found — ask the user whether to proceed to Projects.
                def _after_noconfig(proceed: bool) -> None:
                    if not proceed:
                        self.exit()
                        return
                    self._nav = [ProjectsFrame()]
                    self._activate_top()
                    self._refresh_header()
                self.push_screen(NoConfigModal(), _after_noconfig)

        self.push_screen(WorkspaceScreen(detected), _apply)

    def _load_account(self) -> None:
        acct = fetch_active_account()
        self.call_from_thread(self._set_account, acct or "n/a")

    def _set_account(self, acct: str) -> None:
        self._account = acct
        self._refresh_header()

    # ── Key hints ────────────────────────────────────────────────────

    @staticmethod
    def _key_hints() -> str:
        pairs = [
            ("<enter>",  "Drill In"),
            ("<esc>",    "Back"),
            ("</>",      "Filter"),
            ("<space>",  "Toggle"),
            ("<a>",      "Select All"),
            ("<A>",      "Deselect All"),
            ("<d>",      "Delete"),
            ("<w>",      "Workspace"),
            ("<g>",      "Generate"),
            ("<l>",      "Command Log"),
            ("<p>",      "Preselects"),
            ("<:>",      "Command"),
            ("<r>",      "Refresh"),
            ("<?>",      "Help"),
            ("<q>",      "Quit"),
        ]
        # k9s-style: distribute hints column-major, max 6 rows per column,
        # then concatenate each row horizontally to fill available width.
        max_rows = 6
        key_w = max(len(k) for k, _ in pairs)
        desc_w = max(len(d) for _, d in pairs)
        n_cols = (len(pairs) + max_rows - 1) // max_rows
        grid = [pairs[c * max_rows:(c + 1) * max_rows] for c in range(n_cols)]
        rows = []
        for r in range(max_rows):
            parts = []
            for col in grid:
                if r < len(col):
                    key, desc = col[r]
                    parts.append(
                        f"[#1e90ff]{key:<{key_w}}[/] [#8b949e]{desc:<{desc_w}}[/]  "
                    )
                else:
                    parts.append(" " * (key_w + desc_w + 3))
            rows.append("".join(parts))
        return "\n".join(rows)

    # ── Log callback (called from worker threads) ─────────────────

    def _log_cmd(self, msg: str) -> None:
        self.call_from_thread(self._append_log, msg)

    def _append_log(self, msg: str) -> None:
        self.query_one("#cmd-log", RichLog).write(msg)

    # ── Navigation ───────────────────────────────────────────────────

    def _activate_top(self) -> None:
        frame = self._nav[-1]
        table = self.query_one("#nav-table", DataTable)
        detail = self.query_one("#detail-view", RichLog)
        self.query_one("#cmd-bar").display = False
        self.query_one("#filter-bar").display = False
        # Always rebuild ConfiguredClustersFrame so newly selected clusters appear.
        if isinstance(frame, ConfiguredClustersFrame):
            frame.rows = None

        if frame.is_detail():
            table.display = False
            detail.display = True
            if frame.detail_text is not None:
                self._render_frame(frame)
            else:
                detail.clear()
                self._status = f"Loading {frame.title()}…"
                self._refresh_header()
                self.run_worker(lambda: frame.load(self), thread=True)
        else:
            detail.display = False
            table.display = True
            if frame.rows is not None:
                self._render_frame(frame)
            else:
                table.clear(columns=True)
                self._status = f"Loading {frame.title()}…"
                self._refresh_header()
                self.run_worker(lambda: frame.load(self), thread=True)

        self._update_breadcrumb()

    def _render_frame(self, frame: NavFrame) -> None:
        if frame is not self._nav[-1]:
            return  # stale callback for a frame we've since navigated away from

        # Apply pre-selections BEFORE rendering so row_values picks up ✓ marks.
        if self._preselect and isinstance(frame, ResourceListFrame) and frame.kind.key == "gke":
            proj = frame.project_id
            log = self.query_one("#cmd-log", RichLog)
            log.write(f"[#f5a623]preselect:[/] checking {len(self._preselect)} kubeconfig context(s)")
            for p, l, c in sorted(self._preselect):
                log.write(f"  [dim]wanted :[/] [cyan]{p}[/] / [white]{c}[/] / [dim]{l}[/]")
            for r in frame.rows or []:
                loc = r.get("location") or r.get("zone", "")
                name = r.get("name", "")
                matched = (proj, loc, name) in self._preselect
                log.write(
                    f"  [dim]cluster:[/] [cyan]{proj}[/] / [white]{name}[/] / [dim]{loc}[/]"
                    f"  → {'[green]YES[/]' if matched else '[red]no[/]'}"
                )
                if matched:
                    key = self.sel_key(frame, r)
                    if key not in self._selected:
                        self._selected[key] = gke_cluster_from_row(proj, r)
                        log.write(f"    [green]✓ pre-selected[/]  {key}")
                    self._preselect.discard((proj, loc, name))
            if not self._preselect:
                self._preselect = set()

        if frame.is_detail():
            detail = self.query_one("#detail-view", RichLog)
            detail.clear()
            detail.write(frame.detail_text or "")
        else:
            table = self.query_one("#nav-table", DataTable)
            row = table.cursor_row
            frame.visible = [r for r in (frame.rows or []) if frame.matches_filter(self, r)]
            table.clear(columns=True)
            table.add_columns(*frame.columns())
            for r in frame.visible:
                table.add_row(*frame.row_values(self, r), key=frame.row_key(r))
            if frame.visible and 0 <= row < len(frame.visible):
                table.move_cursor(row=row)

        self._status = "Ready"
        self._update_breadcrumb()
        self._refresh_header()

    def _load_error(self, msg: str) -> None:
        self._status = f"Error: {msg}"
        self._refresh_header()

    def _update_breadcrumb(self) -> None:
        frame = self._nav[-1]
        title = f" {frame.title()}"
        if not frame.is_detail() and frame.rows is not None:
            if frame.filter_text and frame.visible is not None:
                title += f"[{len(frame.visible)}/{len(frame.rows)}]"
            else:
                title += f"[{len(frame.rows)}]"
        if frame.filter_text:
            title += f"  filter:'{frame.filter_text}'"
        if self._selected:
            title += f"  selected:{len(self._selected)}"
        self.query_one("#table-wrap").border_title = title
        self.query_one("#tab-clusters", Static).update(f"<{frame.tab_label()}>")

    def _current_row(self) -> dict | None:
        frame = self._nav[-1]
        if frame.is_detail() or not frame.visible:
            return None
        table = self.query_one("#nav-table", DataTable)
        i = table.cursor_row
        return frame.visible[i] if 0 <= i < len(frame.visible) else None

    def _current_project_id(self) -> str | None:
        for frame in reversed(self._nav):
            pid = getattr(frame, "project_id", None)
            if pid:
                return pid
        return None

    @staticmethod
    def sel_key(frame: "ResourceListFrame", row: dict) -> str:
        return f"{frame.project_id}:{frame.kind.key}:{frame.kind.row_key_fn(row)}"

    @on(DataTable.RowSelected)
    def _on_row_selected(self, event: DataTable.RowSelected) -> None:
        frame = self._nav[-1]
        if frame.is_detail() or not frame.rows:
            return
        row = self._current_row()
        if row is None:
            return

        # ConfiguredClustersFrame: Enter opens rename modal instead of navigating.
        if isinstance(frame, ConfiguredClustersFrame):
            proj = row["_project_id"]
            name = row["name"]
            loc = row["location"]
            ctx_name = row.get("_context_name", "")
            key = frame._sel_key(proj, name, loc)
            current_name = self._cluster_names.get(
                key, ConfiguredClustersFrame.default_context_name(ctx_name, proj, loc, name)
            )
            def _apply_rename(new_name: str | None) -> None:
                if new_name:
                    self._cluster_names[key] = new_name
                    self._render_frame(frame)
            self.push_screen(RenameContextModal(current_name), _apply_rename)
            return

        next_frame = frame.on_enter(self, row)
        if next_frame is not None:
            self._nav.append(next_frame)
            self._activate_top()

    @on(DataTable.HeaderSelected)
    def _on_header_selected(self, event: DataTable.HeaderSelected) -> None:
        frame = self._nav[-1]
        if frame.is_detail() or not frame.rows:
            return
        idx = event.column_index
        marker = (id(frame), idx)
        if self._sort_col == marker:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_col = marker
            self._sort_reverse = False
        frame.rows.sort(key=frame.sort_key(idx, self), reverse=self._sort_reverse)
        self._render_frame(frame)

    # ── Command bar ──────────────────────────────────────────────────

    @on(PromptBar.Submitted, "#cmd-bar")
    def _cmdbar_submit(self, event: PromptBar.Submitted) -> None:
        text = event.value.strip().lstrip(":").lower()
        event.bar.clear()
        event.bar.display = False
        self.query_one("#nav-table", DataTable).focus()
        if not text:
            return

        if text in ("projects", "proj", "project"):
            self._nav = [ProjectsFrame()]
            self._activate_top()
            return

        if text in ("configured", "config", "home"):
            if self._kubeconfig_contexts:
                self._nav = [ConfiguredClustersFrame(self._kubeconfig_contexts)]
                self._activate_top()
            else:
                self.notify("No configured clusters found.", severity="warning")
            return

        kind = resolve_alias(text)
        if kind is None:
            self.notify(f"Unknown command: {text}", severity="warning")
            return
        project_id = self._current_project_id()
        if project_id is None:
            self.notify("Select a project first.", severity="warning")
            return
        self._nav.append(ResourceListFrame(project_id, kind))
        self._activate_top()

    # ── Filter bar ───────────────────────────────────────────────────

    @on(PromptBar.Changed, "#filter-bar")
    def _filterbar_changed(self, event: PromptBar.Changed) -> None:
        frame = self._nav[-1]
        if frame.is_detail():
            return
        frame.filter_text = event.value
        self._render_frame(frame)

    @on(PromptBar.Submitted, "#filter-bar")
    def _filterbar_submit(self, event: PromptBar.Submitted) -> None:
        event.bar.display = False
        self.query_one("#nav-table", DataTable).focus()

    # ── Header info ──────────────────────────────────────────────────

    def _refresh_header(self) -> None:
        current_project = self._current_project_id() or "—"

        lines = [
            f"[#f5a623]Organization:[/]  [bold white]{self._organization}[/]",
            f"[#f5a623]Google User :[/]  [bold white]{self._account}[/]",
            f"[#f5a623]Selected    :[/]  [bold white]{len(self._selected)} cluster(s)[/]",
            f"[#f5a623]Project     :[/]  [bold white]{current_project}[/]",
            f"[#f5a623]Workspace   :[/]  [bold white]{self._workspace}[/]",
            f"[#f5a623]G9s Rev     :[/]  [bold white]v{__version__}[/]",
            f"[#f5a623]Status:[/]  [bold white]{self._status}[/]",
        ]
        self.query_one("#info-block", Static).update("\n".join(lines))

    # ── Actions ──────────────────────────────────────────────────────

    def action_toggle_sel(self) -> None:
        frame = self._nav[-1]
        row = self._current_row()
        if row is None:
            return
        if isinstance(frame, ConfiguredClustersFrame):
            key = frame.row_key(row)
            if key in self._selected:
                del self._selected[key]
            else:
                proj = row["_project_id"]
                loc = row["location"]
                from .gcloud import location_type as _lt
                self._selected[key] = GKECluster(
                    name=row["name"], project_id=proj, location=loc,
                    location_type=_lt(loc), status="CONFIGURED",
                    num_nodes=0, k8s_version="?",
                )
            self._render_frame(frame)
            self._refresh_header()
        elif isinstance(frame, ResourceListFrame) and frame.kind.selectable:
            key = self.sel_key(frame, row)
            if key in self._selected:
                del self._selected[key]
            else:
                self._selected[key] = gke_cluster_from_row(frame.project_id, row)
            self._render_frame(frame)
            self._refresh_header()

    def action_select_all(self) -> None:
        frame = self._nav[-1]
        if isinstance(frame, ConfiguredClustersFrame):
            for row in frame.visible or []:
                proj, loc, name = row["_project_id"], row["location"], row["name"]
                key = frame.row_key(row)
                from .gcloud import location_type as _lt
                if key not in self._selected:
                    self._selected[key] = GKECluster(
                        name=name, project_id=proj, location=loc,
                        location_type=_lt(loc), status="CONFIGURED",
                        num_nodes=0, k8s_version="?",
                    )
            self._render_frame(frame)
            self._refresh_header()
        elif isinstance(frame, ResourceListFrame) and frame.kind.selectable:
            for row in frame.visible or []:
                self._selected[self.sel_key(frame, row)] = gke_cluster_from_row(frame.project_id, row)
            self._render_frame(frame)
            self._refresh_header()

    def action_deselect_all(self) -> None:
        frame = self._nav[-1]
        if isinstance(frame, (ConfiguredClustersFrame, ResourceListFrame)):
            if isinstance(frame, ConfiguredClustersFrame):
                for row in frame.visible or []:
                    self._selected.pop(frame.row_key(row), None)
            elif isinstance(frame, ResourceListFrame) and frame.kind.selectable:
                for row in frame.visible or []:
                    self._selected.pop(self.sel_key(frame, row), None)
            self._render_frame(frame)
            self._refresh_header()

    def action_delete_configured(self) -> None:
        """Remove a cluster from the :configured screen — deselects it and
        removes it from _kubeconfig_contexts so it won't appear on next load
        and won't be included in the next Generate."""
        frame = self._nav[-1]
        if not isinstance(frame, ConfiguredClustersFrame):
            return
        row = self._current_row()
        if row is None:
            return
        proj = row["_project_id"]
        name = row["name"]
        loc = row["location"]
        key = frame._sel_key(proj, name, loc)
        # Remove from selection.
        self._selected.pop(key, None)
        # Remove from persisted kubeconfig context list (matches on proj/loc/cluster).
        self._kubeconfig_contexts = [
            t for t in self._kubeconfig_contexts
            if not (t[0] == proj and t[1] == loc and t[2] == name)
        ]
        # Force a fresh rebuild on the next activate.
        frame.rows = None
        self._activate_top()
        self._refresh_header()

    def action_workspace(self) -> None:
        current = self._workspace
        def _apply(path: str | None) -> None:
            if path:
                self._workspace = path.strip() or current
            self._refresh_header()
        self.push_screen(WorkspaceScreen(current), _apply)

    def action_generate(self) -> None:
        sel = list(self._selected.values())
        if not sel:
            self.notify(
                "Select at least one cluster first (GKE Clusters screen).",
                severity="warning",
                title="Nothing selected",
            )
            return
        self.push_screen(GenerateScreen(sel, self._workspace, self._cluster_names))

    def action_toggle_log(self) -> None:
        panel = self.query_one("#cmd-panel")
        self._log_visible = not self._log_visible
        panel.display = self._log_visible
        if self._log_visible:
            self.query_one("#cmd-log", RichLog).scroll_end(animate=False)

    def action_toggle_preselect(self) -> None:
        panel = self.query_one("#preselect-panel")
        self._preselect_visible = not self._preselect_visible
        panel.display = self._preselect_visible
        if self._preselect_visible:
            log = self.query_one("#preselect-log", RichLog)
            log.clear()
            config_path = str(Path(self._workspace) / "config")
            log.write(f"[#f5a623]Kubeconfig:[/]  [bold white]{config_path}[/]\n")
            if self._kubeconfig_contexts:
                for project_id, location, cluster_name, ctx_name in self._kubeconfig_contexts:
                    log.write(
                        f"  [green]✓[/]  [dim]{ctx_name}[/]  →  "
                        f"[white]{cluster_name}[/]  [#f5a623]{project_id}[/]  [dim]{location}[/]"
                    )
            else:
                log.write("[dim]  (no existing kubeconfig found or no GKE contexts)[/]")

    def action_refresh(self) -> None:
        frame = self._nav[-1]
        frame.rows = None
        frame.detail_text = None
        self._activate_top()

    def action_help_modal(self) -> None:
        self.push_screen(HelpScreen())

    def action_nav_back(self) -> None:
        cmd_bar = self.query_one("#cmd-bar", PromptBar)
        filter_bar = self.query_one("#filter-bar", PromptBar)
        if cmd_bar.display:
            cmd_bar.clear()
            cmd_bar.display = False
            self.query_one("#nav-table", DataTable).focus()
            return
        if filter_bar.display:
            filter_bar.clear()
            filter_bar.display = False
            frame = self._nav[-1]
            if not frame.is_detail() and frame.filter_text:
                frame.filter_text = ""
                self._render_frame(frame)
            self.query_one("#nav-table", DataTable).focus()
            return
        if len(self._nav) > 1:
            self._nav.pop()
            self._activate_top()

    def action_open_cmdbar(self) -> None:
        bar = self.query_one("#cmd-bar", PromptBar)
        bar.clear()
        bar.display = True
        bar.focus()

    def action_open_filter(self) -> None:
        frame = self._nav[-1]
        if frame.is_detail():
            return
        bar = self.query_one("#filter-bar", PromptBar)
        bar.value = frame.filter_text
        bar.display = True
        bar.focus()

    def action_move_down(self) -> None:
        self.query_one("#nav-table", DataTable).action_cursor_down()

    def action_move_up(self) -> None:
        self.query_one("#nav-table", DataTable).action_cursor_up()
