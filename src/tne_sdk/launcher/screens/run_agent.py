"""
TNE-SDK Launcher: Run Agent Screen

Two-column live dashboard:
  Left  : StatusPanel (integrity/power bars, location, last action, memory stats)
  Right : LogView (scrolling color-coded log)

The Agent runs as an asyncio Task inside Textual's own event loop.
TickSummary callbacks drive the status panel updates each tick.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical

from ..widgets.split_container import ResizableSplit
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Input, Label, Static

from ..widgets.log_view import LogView, WidgetLogHandler
from ..widgets.status_panel import StatusPanel
from ...models import TickSummary


# ── Directive input modal ─────────────────────────────────────────────────── #

class DirectiveModal(ModalScreen[str | None]):
    """Single-line text input for hotloading a directive."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter",  "submit", "Send"),
    ]

    def compose(self) -> ComposeResult:
        with Static(id="form-container"):
            yield Label("Enter directive for agent:")
            yield Input(placeholder="e.g. Prioritise mining in Rust Wastes", id="directive-input")
            yield Button("Send  \\[Enter]",  variant="primary", id="btn-send")
            yield Button("Cancel \\[Esc]",   variant="default", id="btn-cancel")

    def on_mount(self) -> None:
        self.query_one("#directive-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-send":
            self.action_submit()
        else:
            self.action_cancel()

    def action_submit(self) -> None:
        text = self.query_one("#directive-input", Input).value.strip()
        self.dismiss(text or None)

    def action_cancel(self) -> None:
        self.dismiss(None)




# ── Run agent screen ──────────────────────────────────────────────────────── #

class RunAgentScreen(Screen):
    """Live agent dashboard."""

    BINDINGS = [
        Binding("q",      "stop_agent",  "Stop",          priority=True),
        Binding("ctrl+d", "directive",   "Add Directive"),
        Binding("ctrl+l", "toggle_log",  "Log Level"),
    ]

    def __init__(self, profile: dict[str, Any], log_dir: Path) -> None:
        super().__init__()
        self._profile  = profile
        self._log_dir  = log_dir
        self._agent:   Any | None  = None
        self._task:    asyncio.Task | None = None
        self._log_handler: WidgetLogHandler | None = None

    def compose(self) -> ComposeResult:
        name = self._profile.get("name", "?")
        yield Static(
            f" ◈ NULL EPOCH - {name}   \\[Q] Stop",
            id="run-header",
        )
        with ResizableSplit(initial_left=48, min_left=30, min_right=40, id="run-body"):
            yield StatusPanel(id="status-column")
            with Vertical(id="log-column"):
                with Horizontal(id="log-toolbar"):
                    yield Static("LOG", id="log-toolbar-title")
                    yield Button("📋", id="btn-copy-logs", classes="log-tool-btn")
                    yield Button("💾", id="btn-save-logs", classes="log-tool-btn")
                yield LogView(id="log-view")
        yield Static(
            "  \\[Ctrl+D] Add Directive   \\[Ctrl+L] Cycle Log Level   \\[Q] Stop Agent",
            id="run-footer",
        )

    def on_mount(self) -> None:
        self._attach_log_handler()
        self._start_agent()

    def on_unmount(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._detach_log_handler()

    # ── Logging ──────────────────────────────────────────────────────────── #

    def _attach_log_handler(self) -> None:
        log_view: LogView = self.query_one("#log-view", LogView)
        self._log_handler = WidgetLogHandler(log_view)
        self._log_handler.setLevel(logging.INFO)

        # Attach to tne_sdk logger and prevent propagation to root (avoids dupes)
        # Level must be at VERBOSE (5) so payload records aren't filtered out
        # before reaching the handler - the handler's own level gates visibility.
        from ..widgets.log_view import VERBOSE
        tne_logger = logging.getLogger("tne_sdk")
        tne_logger.setLevel(VERBOSE)
        tne_logger.addHandler(self._log_handler)
        tne_logger.propagate = False

    def _detach_log_handler(self) -> None:
        if self._log_handler:
            tne_logger = logging.getLogger("tne_sdk")
            tne_logger.removeHandler(self._log_handler)
            tne_logger.propagate = True
            self._log_handler = None

    # ── Agent lifecycle ───────────────────────────────────────────────────── #

    def _start_agent(self) -> None:
        from ...config import AgentConfig
        from ...client import TNEClient
        from ...llm.providers import provider_from_profile
        from ...agent import Agent
        from ...memory.local_memory import LocalMemory

        profile = self._profile
        cfg     = AgentConfig.from_dict(profile)

        raw_host = profile.get("game_host", "api.null.firespawn.ai")
        secure   = "firespawn.ai" in raw_host or (
            not raw_host.startswith("localhost") and not raw_host.startswith("127.")
        )

        client = TNEClient(api_key=profile["api_key"], host=raw_host, secure=secure)
        memory = LocalMemory(agent_name=profile["name"], db_path=self._log_dir)
        llm    = provider_from_profile(profile, timeout=cfg.llm_timeout)

        self._agent = Agent(
            config           = cfg,
            client           = client,
            memory           = memory,
            llm_provider     = llm,
            name             = profile["name"],
            on_tick_summary  = self._handle_summary,
            log_payloads     = cfg.log_payloads,
            log_dir          = self._log_dir,
        )

        # Schedule agent on Textual's event loop
        self._task = asyncio.get_running_loop().create_task(
            self._agent.run(),
            name=f"agent-{profile['name']}",
        )
        self._task.add_done_callback(self._on_agent_done)

    def _on_agent_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            self.notify("Agent stopped.", severity="warning")
        elif exc := task.exception():
            self.notify(f"Agent crashed: {exc}", severity="error")
        else:
            self.notify("Agent finished.")

    # ── TickSummary callback ──────────────────────────────────────────────── #

    def _handle_summary(self, summary: TickSummary) -> None:
        """Fired from the agent's asyncio task after each action tick."""
        try:
            panel: StatusPanel = self.query_one("#status-column", StatusPanel)
            panel.update_from_summary(summary)
        except Exception:
            pass  # screen may have been unmounted

    # ── Actions ──────────────────────────────────────────────────────────── #

    def action_stop_agent(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        elif self._task and self._task.done() and not self._task.cancelled():
            # Consume the exception so pop_screen doesn't re-raise it
            try:
                self._task.result()
            except Exception:
                pass
        self.app.pop_screen()

    def action_directive(self) -> None:
        self.app.push_screen(DirectiveModal(), callback=self._on_directive)

    def _on_directive(self, text: str | None) -> None:
        if not text or not self._agent:
            return
        try:
            self._agent.add_directive(text)
            self.notify(f"Directive injected: {text[:60]}")
        except Exception as exc:
            self.notify(str(exc), severity="error")

    def action_toggle_log(self) -> None:
        if not self._log_handler:
            return
        # Cycle: INFO -> DEBUG -> VERBOSE -> INFO
        from ..widgets.log_view import VERBOSE
        current = self._log_handler.level
        if current >= logging.INFO:
            new_level = logging.DEBUG
            label = "DEBUG"
        elif current >= logging.DEBUG:
            new_level = VERBOSE
            label = "VERBOSE (payloads visible)"
        else:
            new_level = logging.INFO
            label = "INFO"
        self._log_handler.setLevel(new_level)
        self.notify(f"Log level: {label}")

    def _include_payloads(self) -> bool:
        """True when the log handler is at VERBOSE level (payloads visible)."""
        from ..widgets.log_view import VERBOSE
        return self._log_handler is not None and self._log_handler.level <= VERBOSE

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-copy-logs":
            self._copy_logs_to_clipboard()
        elif event.button.id == "btn-save-logs":
            self._save_logs_to_disk()

    def _copy_logs_to_clipboard(self) -> None:
        log_view: LogView = self.query_one("#log-view", LogView)
        text = log_view.export_text(include_payloads=self._include_payloads())
        if not text:
            self.notify("No logs to copy.", severity="warning")
            return
        try:
            import pyperclip
            pyperclip.copy(text)
            self.notify("Logs copied to clipboard.")
        except ImportError:
            # Fallback: use platform clip command directly
            self._platform_copy(text)
        except Exception as exc:
            self.notify(f"Copy failed: {exc}", severity="error")

    def _platform_copy(self, text: str) -> None:
        """Fallback clipboard copy using OS commands."""
        import shutil, subprocess, sys
        try:
            if sys.platform == "win32":
                proc = subprocess.Popen(["clip"], stdin=subprocess.PIPE)
                proc.communicate(text.encode("utf-16le"))
            elif sys.platform == "darwin":
                proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
                proc.communicate(text.encode("utf-8"))
            else:
                # Try xclip first, then xsel
                cmd = None
                if shutil.which("xclip"):
                    cmd = ["xclip", "-selection", "clipboard"]
                elif shutil.which("xsel"):
                    cmd = ["xsel", "--clipboard", "--input"]
                if cmd is None:
                    self.notify(
                        "Install xclip or xsel for clipboard support",
                        severity="warning",
                    )
                    return
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                proc.communicate(text.encode("utf-8"))
            self.notify("Logs copied to clipboard.")
        except Exception as exc:
            self.notify(f"Copy failed: {exc}", severity="error")

    def _save_logs_to_disk(self) -> None:
        log_view: LogView = self.query_one("#log-view", LogView)
        data = log_view.export_json(include_payloads=self._include_payloads())
        if data == "[]":
            self.notify("No logs to save.", severity="warning")
            return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = self._profile.get("name", "agent")
        default_name = f"logs_{name}_{ts}.json"
        initial_dir = str(self._log_dir)

        # Snapshot the JSON before entering the thread
        self._pending_save_data = data

        async def _pick_and_save() -> None:
            loop = asyncio.get_running_loop()
            chosen = await loop.run_in_executor(
                None, self._open_save_dialog, initial_dir, default_name
            )
            if chosen == "__NO_TKINTER__":
                # tkinter not installed - save to default location automatically
                path = self._log_dir / default_name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(self._pending_save_data, encoding="utf-8")
                self.notify(
                    f"Saved → {path}  (install python3-tk for file picker)",
                )
                return
            if not chosen:
                return
            try:
                path = Path(chosen)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(self._pending_save_data, encoding="utf-8")
                self.notify(f"Saved → {path}")
            except Exception as exc:
                self.notify(f"Save failed: {exc}", severity="error")

        asyncio.get_running_loop().create_task(_pick_and_save())

    @staticmethod
    def _open_save_dialog(initial_dir: str, default_name: str) -> str | None:
        """Open a native OS save-file dialog (runs in a worker thread).

        Returns the chosen path, ``"__NO_TKINTER__"`` if tkinter is
        unavailable, or None if the user cancelled.
        """
        try:
            import tkinter as tk
            from tkinter import filedialog
        except ImportError:
            return "__NO_TKINTER__"

        try:
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            path = filedialog.asksaveasfilename(
                parent=root,
                title="Save Logs",
                initialdir=initial_dir,
                initialfile=default_name,
                defaultextension=".json",
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            )
            root.destroy()
            return path or None
        except Exception:
            return None
