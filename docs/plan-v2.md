# Liscribe v2 — Phase 1: Strip to Engine Foundation

> **Goal:** Take the v1 repo on a new branch and reduce it to only the files
> that carry forward verbatim into v2. Leave nothing that references Textual,
> Click, shell aliases, or the v1 TUI. The result is a clean, passing test
> suite over the engine layer only — no UI, no entry point yet.
>
> **Done when:** `pytest` runs and all kept tests pass. No import errors.
> No references to removed modules anywhere in the kept files.

---

## Branch

```bash
git checkout -b v2
```

Work entirely on this branch. `main` stays as the v1 archive.

---

## Step 1 — Delete files that do not carry forward

Delete every file in the list below. Do not modify them first — delete.

**src/liscribe/ — delete these:**
```
app.py
cli.py
dictation.py
dictation_launchd.py
logging_setup.py
menubar.py
overlay.py
rec.css
shell_alias.py
__main__.py
screens/__init__.py
screens/base.py
screens/devices_screen.py
screens/help_screen.py
screens/home.py
screens/modals.py
screens/preferences.py
screens/prefs_alias.py
screens/prefs_dependencies.py
screens/prefs_dictation.py
screens/prefs_general.py
screens/prefs_microphone.py
screens/prefs_save_location.py
screens/prefs_transcripts.py
screens/prefs_whisper.py
screens/recording.py
screens/top_bar.py
screens/transcribing.py
screens/transcripts.py
```

**tests/ — delete these:**
```
test_cli.py
test_dictation_launchd.py
test_keybindings.py
test_recording_screen.py
test_shell_alias.py
```

**Root — delete these:**
```
install.sh
uninstall.sh
config.example.json
```

**docs/ — delete all:**
```
docs/architecture.md
docs/dictation-setup.md
docs/navigation.mmd
docs/ui.md
docs/WORKFLOWS.md
```

**Cursor plans — delete all:**
```
.cursor/plans/
```

---

## Step 2 — Files to keep (touch nothing in them yet)

These carry forward verbatim. Do not edit them in this phase.

```
src/liscribe/__init__.py
src/liscribe/recorder.py
src/liscribe/transcriber.py
src/liscribe/notes.py
src/liscribe/output.py
src/liscribe/transcribe_worker.py
src/liscribe/config.py
src/liscribe/platform_setup.py
src/liscribe/waveform.py

tests/__init__.py
tests/test_recorder.py
tests/test_transcriber.py
tests/test_notes.py
tests/test_output.py
tests/test_transcribe_worker.py
tests/test_config.py
```

---

## Step 3 — Rewrite pyproject.toml

Replace the entire file with the following. This removes all v1 UI dependencies
and adds nothing new yet — new UI deps come in Phase 2.

```toml
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "liscribe"
version = "2.0.0"
description = "100% offline Mac audio recorder and transcriber"
readme = "README.md"
requires-python = ">=3.10"
license = {text = "GPL-3.0"}

dependencies = [
    "faster-whisper>=1.1.0",
    "sounddevice>=0.5.0",
    "scipy>=1.11.0",
    "numpy>=1.24.0,<2.0.0",
    "pyperclip>=1.9.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-cov>=4.0",
]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

---

## Step 4 — Rewrite requirements.txt

```
faster-whisper>=1.1.0
sounddevice>=0.5.0
scipy>=1.11.0
numpy>=1.24.0,<2.0.0
pyperclip>=1.9.0
```

---

## Step 5 — Rewrite README.md

Replace with a minimal placeholder so the repo is honest about its state.

```markdown
# Liscribe v2

> Work in progress — v2 rewrite. See `main` branch for the stable v1.

100% offline Mac audio recorder and transcriber.
Menu bar resident. No terminal required after install.

## Status

Phase 1 complete — engine layer only. UI layer in progress.

## Engine modules (stable)

- `recorder.py` — audio capture, dual-source BlackHole recording
- `transcriber.py` — faster-whisper integration, multi-model
- `output.py` — markdown output, in:/out: labelling, mic bleed suppression
- `notes.py` — timestamped footnotes
- `config.py` — JSON config
- `platform_setup.py` — BlackHole/PortAudio checks
- `waveform.py` — audio level data
```

---

## Step 6 — Run tests and confirm green

```bash
pip install -e ".[dev]"
pytest
```

**Expected:** all tests in the kept test files pass. If any test imports a
deleted module, fix the import — do not restore the deleted file.

Common import fixes needed:
- Any test that imports from `liscribe.cli` → delete that test or the specific
  test function if it is not testing engine behaviour
- Any test that imports from `liscribe.app` or `liscribe.screens` → delete

**Acceptance criteria for Phase 1 complete:**
- [ ] `pytest` exits 0
- [ ] No file in `src/liscribe/` imports from a deleted module
- [ ] No file in `tests/` imports from a deleted module
- [ ] `src/liscribe/screens/` directory does not exist
- [ ] `pyproject.toml` contains no reference to `textual`, `click`, `pyfiglet`, `pynput`, `rich`, `PyYAML`
- [ ] `git status` shows only the deletions and rewrites above — no untracked engine modifications

---

## What Phase 1 does NOT do

- Does not add rumps, pywebview, or any new dependency
- Does not create any UI
- Does not create an entry point or runnable app
- Does not modify any engine logic

The engine files are frozen in this phase. Any bugs found in them during
Phase 1 are noted in a list but not fixed here — fixes happen in Phase 3
(engine integration) where they can be tested against real UI behaviour.

---

## Phase 2 preview (for context only — not part of this phase)

Phase 2 is C4 architecture + new folder structure scaffolding.
No code is written in Phase 2 either — it produces the architecture document
and the empty file tree that Phase 3 fills in.