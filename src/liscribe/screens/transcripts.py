"""Transcripts screen — list .md files from --here and default folders."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shlex
import subprocess
import sys

from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.widgets import Button, Static

from liscribe.config import load_config
from liscribe.output import copy_to_clipboard
from liscribe.screens.base import BackScreen
from liscribe.screens.modals import ConfirmDeleteTranscriptScreen
from liscribe.screens.top_bar import TopBar


class TranscriptsScreen(BackScreen):
    """List transcripts from --here and default folders; copy/open/delete actions."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._paths_by_token: dict[str, Path] = {}

    def compose(self):
        with Vertical(classes="screen-frame"):
            yield TopBar(variant="compact", section="Transcripts")
            with Vertical(classes="screen-body"):
                with ScrollableContainer(id="transcripts-list", classes="scroll-fill"):
                    pass  # filled in on_mount
            with Horizontal(classes="footer-container"):
                yield Static("", classes="spacer-x")
                yield Button("^c Back Home", id="btn-back", classes="btn btn-secondary btn-inline")

    def on_mount(self) -> None:
        self._refresh()

    def _source_folders(self) -> list[tuple[str, Path]]:
        cfg = load_config()
        here_folder = (Path.cwd() / "docs" / "transcripts").resolve()
        default_folder = Path(cfg.get("save_folder", "~/transcripts")).expanduser().resolve()

        seen: set[Path] = set()
        sources: list[tuple[str, Path]] = []
        for label, folder in [("here", here_folder), ("default", default_folder)]:
            if folder in seen:
                continue
            seen.add(folder)
            sources.append((label, folder))
        return sources

    @staticmethod
    def _display_path(path: Path) -> str:
        resolved = path.expanduser().resolve()
        home = Path.home().resolve()
        try:
            rel = resolved.relative_to(home)
        except ValueError:
            return str(resolved)
        rel_posix = rel.as_posix()
        return "~" if rel_posix == "." else f"~/{rel_posix}"

    def _refresh(self) -> None:
        container = self.query_one("#transcripts-list", ScrollableContainer)
        container.remove_children()
        self._paths_by_token.clear()

        token_idx = 0
        for label, folder in self._source_folders():
            md_files = []
            if folder.exists():
                md_files = sorted(folder.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)

            section_children: list[Static | TranscriptRow] = []
            if not md_files:
                section_children.append(Static("No transcripts in this folder.", classes="screen-body-subtitle"))
            else:
                for path in md_files:
                    mtime = datetime.fromtimestamp(path.stat().st_mtime)
                    date_str = mtime.strftime("%d-%m-%Y")
                    token = f"t{token_idx}"
                    token_idx += 1
                    self._paths_by_token[token] = path
                    section_children.append(TranscriptRow(path=path, date_str=date_str, token=token))

            section = Vertical(*section_children, classes="transcript-source")
            section.border_title = f"{label}: {self._display_path(folder)}"
            container.mount(section)

    @staticmethod
    def _guess_open_command(app_value: str, transcript_path: Path) -> list[str]:
        app = (app_value or "").strip()
        if not app:
            app = "cursor"
        if app.lower() in {"default", "system"}:
            if sys.platform == "darwin":
                return ["open", str(transcript_path)]
            if sys.platform.startswith("win"):
                return ["cmd", "/c", "start", "", str(transcript_path)]
            return ["xdg-open", str(transcript_path)]
        return [*shlex.split(app), str(transcript_path)]

    def _copy_transcript(self, path: Path) -> None:
        if not path.exists():
            self.notify("File not found", severity="error")
            return
        text = path.read_text(encoding="utf-8", errors="replace")
        if copy_to_clipboard(text):
            self.notify("Copied to clipboard")
        else:
            self.notify("Could not copy to clipboard", severity="error")

    def _open_transcript(self, path: Path) -> None:
        if not path.exists():
            self.notify("File not found", severity="error")
            return
        cfg = load_config()
        app_value = str(cfg.get("open_transcript_app", "cursor") or "cursor")
        try:
            cmd = self._guess_open_command(app_value, path)
        except ValueError:
            self.notify("Invalid open_transcript_app command.", severity="error")
            return
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.notify(f"Opened with {app_value}: {path.name}")
        except FileNotFoundError:
            self.notify(f"Open command not found: {app_value}", severity="error")
        except Exception as exc:
            self.notify(f"Could not open transcript: {exc}", severity="error")

    def _delete_transcript(self, path: Path) -> None:
        if not path.exists():
            self.notify("File not found", severity="error")
            return
        try:
            path.unlink()
            self.notify(f"Deleted: {path.name}")
            self._refresh()
        except Exception as exc:
            self.notify(f"Could not delete transcript: {exc}", severity="error")

    def _confirm_delete_transcript(self, path: Path) -> None:
        self.app.push_screen(
            ConfirmDeleteTranscriptScreen(path.name),
            lambda confirmed: self._on_delete_confirmed(path, confirmed),
        )

    def _on_delete_confirmed(self, path: Path, confirmed: bool) -> None:
        if confirmed:
            self._delete_transcript(path)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "btn-back":
            self.action_back()
            return

        for action in ("copy", "open", "delete"):
            prefix = f"{action}-"
            if not button_id.startswith(prefix):
                continue
            token = button_id.removeprefix(prefix)
            path = self._paths_by_token.get(token)
            if not path:
                self.notify("File not found", severity="error")
                return
            if action == "copy":
                self._copy_transcript(path)
                return
            if action == "open":
                self._open_transcript(path)
                return
            self._confirm_delete_transcript(path)
            return


class TranscriptRow(Horizontal):
    """One transcript row: date, filename, and actions."""

    def __init__(self, path: Path, date_str: str, token: str, **kwargs) -> None:
        super().__init__(classes="transcript-row", **kwargs)
        self.path = path
        self.date_str = date_str
        self.token = token

    def compose(self):
        self.id = f"row-{self.token}"
        yield Static(f"{self.date_str}   {self.path.name}", id=f"label-{self.token}", classes="transcript-label")
        yield Button("Copy", id=f"copy-{self.token}", classes="btn btn-secondary btn-inline hug-width")
        yield Button("Open", id=f"open-{self.token}", classes="btn btn-secondary btn-inline hug-width")
        yield Button("Delete", id=f"delete-{self.token}", classes="btn btn-secondary btn-inline hug-width")
