# Diff review: Phase 7 Settings

## 1. Diagram / plan alignment

**C4 and Phase 7 structure**
- Matches plan: Settings panel (HTML) + SettingsBridge, bridge delegates to ConfigService, ModelService, AudioService, permissions. No business logic in bridge.
- Tab set matches rubric: General, Models, Hotkeys, Replacements, Deps, Help (six tabs). Rubric sketches for General/Models/Hotkeys/Deps/Help are reflected in the UI.

**Gaps / mismatches**
- **Replacements tab**: Plan says "Replacements (stub only, rules defined in Phase 10)". The diff adds a full pane and table. If the tab is placeholder-only (no CRUD, no persistence), that’s stub-compliant. If it has working add/edit/delete, that’s Phase 10 scope and should be called out or deferred.
- **Help deep-link**: Rubric uses `help://blackhole-setup`-style anchors; code uses URL hash `#help/<anchor>` (e.g. `#help/permissions`). `open_settings_to_help(anchor)` and fragment on load are consistent with that. Confirm that opening Settings via "Help ↗" from Dictate both loads Settings with `#help/permissions` and that the JS that reads `location.hash` and shows the Help tab + section runs after load so the right section is visible.
- **Menu bar**: Rubric shows "🎙 Liscribe"; code uses `MENU_BAR_TITLE = "🎙"`. If "Liscribe" is intended as visible text next to the icon, it may need to come from app name/title elsewhere; otherwise alignment is fine.

**Done-condition check**
- Default model removal: `remove_model` blocks when model is scribe default or dictate model and returns a message — matches "Removing default model prompts replacement before deletion".
- Permissions one-tap: `open_system_settings(pane)` and Deps UI — aligned.
- Close button: Header ✕ calls bridge `close_window()` → app `_close_settings_panel()` — aligned.

---

## 2. Code smells

**permissions_service.py**
- **Two implementations of the same check**: `check_input_monitoring()` (in-process, unsafe from bridge thread) and `_check_input_monitoring_subprocess()` (subprocess, used from `get_all_permissions()`). The real check (pynput listener) is duplicated: once in the function, once in the subprocess script. Any change to the check must be done in two places. The docstring on the public function references "check_input_monitoring_subprocess()" but the actual name is the private `_check_input_monitoring_subprocess()`. Unclear which API is the one to use from which context.
- **Subprocess return logic**: `return result.stdout.strip() == "true" if result.returncode == 0 else False` — if the subprocess crashes (e.g. non-zero exit), we return False. If it exits 0 but prints something else, we also return False. That’s acceptable but easy to misread; a single expression or a short comment would help.

**config_service.py**
- **`_get_app_bundle_path()`**: Uses `for _ in range(3)` to walk up from the executable. The “3” is tied to `.app/Contents/MacOS/<exe>`. If the bundle layout changes or we’re in a different structure, this silently returns None. Magic number and brittle; at least document “max 3 levels” and/or derive depth from path parts.
- **Login item name**: `_set_login_item(False)` uses the hardcoded string `"Liscribe"` in the osascript delete command. If the app is ever renamed or distributed under another name, this breaks. Consider deriving the name from the bundle (e.g. `app_path.stem`).

**app.py**
- **`_window_will_close_no_stop`**: Broad `except (KeyError, AttributeError, TypeError)` and `except Exception` when getting/removing the BrowserView instance. Prevents crashes but can hide real bugs (e.g. wrong object, incomplete teardown). Logging is debug-only. Consider logging at warning level when we fall back so that unexpected states are visible.
- **Panel-specific logic in `_open_panel`**: Scribe (confirm_close), Transcribe (set_window), Settings (set_window, fragment) are all special-cased. Works but makes the method the place where every panel quirk lives; will grow with each new panel.

**settings_bridge.py**
- **`set_config` special cases**: `start_on_login` and `scribe_models` are handled explicitly; everything else goes to `self._config.set(key, value)`. That’s required because start_on_login lives in ui_prefs and not in config.py. The split is a bit opaque from the outside; a one-line comment that “start_on_login and scribe_models are not in config.json” would make the intent clear.

**settings.html**
- **Size and inline style**: Large single file (500+ lines). Inline `style="margin-top:16px"` and similar appear in multiple places; these could be utility classes (e.g. `.mt-16`) in the existing stylesheet to keep layout concerns in one place.
- **Help navigation**: Two mechanisms — (1) initial load with `location.hash` and (2) `window.__liscribeNavigateHelp(anchor)` from Python. Both need to keep the Help tab and detail section in sync. If someone later changes one path and not the other, behaviour can diverge; a short comment that “deep-link uses hash on load; open_help() uses __liscribeNavigateHelp” would help.

---

## 3. Refactor opportunities (if rebuilding)

**Bridge pattern**
- All panels use a bridge (Scribe, Transcribe, Dictate, Settings). There’s no shared base or protocol; each bridge is built by hand. A small base or Protocol (e.g. `set_window(window)`, optional `close_window()`) would make the contract explicit and reduce repeated wiring in `app._open_panel`.

**Panel bootstrap**
- Settings load does: delay → loadMics → loadConfig → applyConfigToGeneral → applyConfigToHotkeys → refreshModels → refreshPermissions. All in one event handler. A small “panel bootstrap” pattern (e.g. list of async data loaders + list of render steps) would make dependencies and order obvious and easier to test or reuse for other panels.

**Permissions “safe” check**
- Input monitoring is either in-process (unsafe from bridge) or subprocess. A single abstraction (e.g. “run this check in a context safe for the current caller”) could pick in-process vs subprocess once, so the pynput check is implemented in one place and the rest of the code just calls “get input monitoring status”.

**Config / UI prefs**
- `start_on_login` lives in ui_prefs.json; everything else in config.json. ConfigService already has a small prefs layer. If more UI-only keys appear, consider a single “UI prefs” API (one file, one interface) so ConfigService doesn’t accumulate more one-off properties and the bridge doesn’t need to know which key lives where.

**Panel registry**
- `_open_panel` encodes name-specific behaviour (scribe confirm_close, transcribe/settings set_window, settings fragment). A small registry (e.g. name → url, size, js_api, optional hooks like set_window, fragment support) would make adding or changing panels a single declarative entry instead of more conditionals in one big method.

**Subprocess helper**
- `_check_input_monitoring_subprocess` embeds a multi-line Python script as a string. Reusable “run this Python snippet in a subprocess and return stdout/exit” would keep the script readable and make it easier to add other “run in subprocess” checks later without duplicating subprocess boilerplate.

---

## Done (follow-up)

- **permissions_service**: Docstring now references `_check_input_monitoring_subprocess()`; added comment on subprocess return line.
- **config_service**: Comment "Walk up at most 3 levels: exe -> MacOS -> Contents -> .app"; login item delete uses `app_path.stem` instead of hardcoded "Liscribe".
- **app.py**: `_window_will_close_no_stop` logs at warning level when BrowserView get or remove fails.
- **settings_bridge**: `set_config` docstring notes that start_on_login and scribe_models are not in config.json.
- **style.css**: Added utilities `.mt-12`, `.mt-16`, `.mt-20`, `.mt-24`, `.mb-12`, `.text-muted`. **settings.html**: Replaced inline margin/color with these classes; added HTML comment that Replacements tab is stub until Phase 10; added JS comments for Help dual path (hash on load vs `__liscribeNavigateHelp`).
