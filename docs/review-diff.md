# Review: Current diff (plan alignment, code smells, refactor)

Be direct. No sugar.

---

## 1. Diagram / plan alignment

### What matches

- **Call chain**  
  Panel → Bridge → Controller → Service → engine is preserved.  
  Scribe: `scribe.html` → `ScribeBridge.open_transcript` → `ScribeController.open_transcript` → `_config` (ConfigService). No new layer skip.

- **Phase 5 spec**  
  Plan: “Complete state (per-file Open Transcript buttons)”. Transcribe already had it; Scribe now has the same. Rubric “Open Transcript uses `open -a AppName file`” is satisfied by both controllers.

- **File layout**  
  No new files in wrong folders. Changes are in existing plan files: `app.py`, bridges, controllers, one panel, output, tests.

### Deviations (not in plan/C4)

- **app.py owns an HTTP server**  
  C4 “Container” shows: Menu Bar (rumps), Panel Layer (pywebview), Services, Engine. It does **not** show a “panel HTML server” or any HTTP process inside the app.  
  The diff adds `_ensure_panel_server()`, `_panel_http_base`, `_panel_server`, and `webview_http.BottleServer.start_server(...)`. That’s new runtime behaviour and lifecycle (server start, port, process lifetime) that are undocumented in the architecture. If the plan is the source of truth, this is drift.

- **app.py patches pywebview’s Cocoa delegate**  
  Plan describes panels as “HTML/CSS view (pywebview)” and doesn’t mention modifying pywebview internals.  
  The diff imports `BrowserView` from `webview.platforms.cocoa` and replaces `WindowDelegate.windowWillClose_` with `_window_will_close_no_stop`. So the app now depends on pywebview’s internal contract (attribute names, cleanup order, `webview.windows`). That’s integration with the platform layer that isn’t in the C4 or component text. Any pywebview change to that delegate or to `BrowserView.instances` can break the app without the plan or diagram signalling the dependency.

- **output.py**  
  `expanduser().resolve()` and `parent.mkdir(...)` are implementation details for “save to ~/transcripts”. Plan/rubric don’t specify where path expansion happens; no diagram conflict.

**Summary:** Behaviour and call chain align with the plan. Two structural additions do not: (1) app-hosted HTTP server for panel HTML, (2) monkey-patch of pywebview’s WindowDelegate. Both should be either reflected in the plan/C4 or explicitly called out as accepted technical debt.

---

## 2. Code smells

- **Duplicated `open_transcript` logic**  
  `ScribeController.open_transcript` and `TranscribeController.open_transcript` are the same: read `_config.open_transcript_app`, then `subprocess.run(["open", ...])` or `subprocess.run(["open", "-a", app, ...])`. Same behaviour in two places. If we change the rule (e.g. escaping, or a different opener), we must remember to change both. **Smell:** duplicated behaviour; should live in one place (e.g. ConfigService or a small shared helper).

- **Duplicated tests for `open_transcript`**  
  `test_scribe_controller.py` and `test_transcribe_controller.py` each have a test that mocks `subprocess.run` and asserts `open`, `-a`, app name, and path. Same scenario, two copies. **Smell:** test duplication; one shared test (or one implementation to test) would be enough.

- **app.py: `_window_will_close_no_stop` duplicates pywebview**  
  The function reimplements the body of pywebview’s `windowWillClose_` (delete from instances, remove from `webview.windows`, clear delegates, load blank HTML, remove view, set `closed`). If pywebview adds or reorders cleanup (e.g. another delegate clear, or a different teardown), we don’t get it. **Smell:** copy-paste of platform behaviour; fragile on pywebview upgrades.

- **app.py: magic string and internal API**  
  `BrowserView.get_instance("window", notification.object())` relies on the `"window"` key and the shape of `notification.object()`. That’s pywebview’s internal API. **Smell:** tight coupling to undocumented internals.

- **app.py: vague type for server**  
  `self._panel_server: object | None` hides the real type (a `BottleServer` or similar from `webview.http`). **Smell:** weak typing; type hint could be the concrete server type or a protocol.

- **Scribe vs Transcribe “Open Transcript” UI**  
  Scribe builds the button in `pollProgress` when `p.is_done && p.md_path`; Transcribe builds it when rendering the done view. Same label and intent, different pattern (dynamic inject vs. one-shot render). **Smell:** no shared abstraction; future changes (e.g. “Copy path”, accessibility) will be done twice unless we introduce a shared pattern or component.

- **No silent failures in the diff**  
  New code uses `logger.warning` / `logger.debug` or propagates; no `except: pass`. OK.

- **Magic strings**  
  `"Open Transcript"` appears in both panels. Minor; only matters if we add i18n.

---

## 3. Refactor opportunities (if rebuilding)

- **Single implementation for “open transcript in external app”**  
  Move the “read config, run `open` or `open -a`” logic to one place: e.g. `ConfigService.open_transcript(file_path: str)` or a small `liscribe.open_transcript` helper used by both controllers. Controllers and bridges only call that; one implementation, one set of tests, one place to change behaviour (e.g. path quoting, or a different command on another OS).

- **Don’t own pywebview’s delegate body**  
  Replace the full-method replacement with something that doesn’t duplicate pywebview’s cleanup:  
  - Option A: Contribute a “don’t stop app when last window closes” option to pywebview and use it.  
  - Option B: Wrap or subclass the delegate so only the “if instances == {}: app.stop_()” branch is overridden, and leave the rest to pywebview.  
  That reduces the risk of breakage on pywebview updates and makes the “we’re a menu bar app” requirement explicit instead of a full copy of the delegate.

- **Document or promote the panel server**  
  If the HTTP server for panel HTML is permanent, either:  
  - Add it to the C4/plan (e.g. “App hosts a small static server for panel HTML; panels load via http://localhost”), or  
  - Extract it to a dedicated service (e.g. `PanelAssetService` or `UIServerService`) created in `main()` and passed into the app, so app.py doesn’t own server lifecycle and the architecture shows “who serves panel HTML”.

- **Shared “completion row” for Scribe and Transcribe**  
  Both panels show “model name + progress + path + Open Transcript”. Right now that’s separate JS and different update paths (polling vs. one-shot). A refactor could introduce a shared script or a small “completion row” contract (data shape + DOM expectations) so both panels render the same row and we add features (e.g. “Copy path”, keyboard access) once.

- **Stronger typing for panel server**  
  Type `_panel_server` as the actual pywebview server type (or a minimal protocol) so call sites and future refactors are clear.

---

## Summary

| Area              | Verdict |
|-------------------|--------|
| Diagram/plan      | Call chain and Phase 5 behaviour match. App-hosted HTTP server and pywebview delegate patch are not in the plan/C4. |
| Code smells       | Duplicate `open_transcript` in two controllers (and two tests); app.py duplicates and depends on pywebview’s delegate; weak type for `_panel_server`; no shared “completion row” between panels. |
| Refactor          | Centralise open-transcript behaviour; avoid copying pywebview’s delegate; document or extract panel server; consider shared completion-row UI. |

---

## Refactors applied (post-review)

- **Single `open_transcript` implementation:** Logic moved to `ConfigService.open_transcript(file_path)`. `ScribeController` and `TranscribeController` delegate to `self._config.open_transcript(file_path)`. One place to change behaviour; duplicate controller logic removed.
- **Tests:** ConfigService has two tests (default app → `open path`; custom app → `open -a App path`) with `subprocess.run` mocked. Controller tests now assert delegation to `config.open_transcript(path)` only.
- **Typing:** `_panel_server` is now `webview_http.BottleServer | None`.
- **Documentation:** Comment above `_window_will_close_no_stop` notes that it duplicates pywebview's `windowWillClose_` and is fragile on upgrade. Comment on `_panel_http_base` / `_ensure_panel_server` documents why panel HTML is served over HTTP.
- **Not done:** Delegate body still duplicated (would require pywebview change or a wrapper). Shared completion-row UI between Scribe and Transcribe not implemented (left as future work).
