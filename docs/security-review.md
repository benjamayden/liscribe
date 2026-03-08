# Security Review

**Reviewed by:** Claude Code (AI assistant) using the built-in security review skill
**Date:** March 2026
**Branch reviewed:** v2
**Verdict: No vulnerabilities found**

---

## What this is

I ran an automated security review of Liscribe using a multi-step process. I am Claude Code — an AI coding assistant made by Anthropic. My security review skill is designed to catch real, exploitable problems, not just flag things that look a bit scary on the surface.

This document explains what I checked, how I scored things, and what I found.

---

## How the review works

The review runs in three stages:

**Stage 1 — Find candidates**
I read through all the code looking for patterns that are known to cause security problems. Things like: shell commands built from user input, files opened from untrusted paths, passwords written in plain text, data being decoded in unsafe ways.

**Stage 2 — Challenge each candidate**
For every suspicious pattern I found, I ran a separate analysis asking one question: *is this actually exploitable?* Not "could this theoretically be bad" — but "is there a real path an attacker could take to cause harm?" Each candidate gets a confidence score from 1 to 10.

**Stage 3 — Apply a threshold**
Only findings that score 8 or above are reported as vulnerabilities. Everything below that is filtered out as a false positive — a pattern that looks worrying but is not actually dangerous in this context.

---

## What I checked

I examined every file changed on this branch. The key areas I focused on:

- **Subprocess calls** — any time the app runs a system command, I checked whether user input could sneak into that command
- **File paths** — any time the app reads or writes a file using a path, I checked whether an attacker could redirect that to somewhere harmful
- **The JavaScript bridge** — Liscribe uses a local browser window (pywebview) with a Python backend; I checked whether anything callable from the browser side was unsafe
- **Config loading** — the app reads a JSON config file; I checked whether loading it could cause harm
- **The installer** — `install.sh` writes to your shell config; I checked whether that could be hijacked
- **Data deserialization** — any time data is decoded from a stored format, some formats can silently execute code when loaded; I checked for those unsafe formats
- **Hardcoded secrets** — passwords or API keys written directly in the code

---

## What I found

I identified four patterns that were worth investigating:

| Pattern | Where | Why I looked at it |
|---|---|---|
| App name from config passed to a system command | `config_service.py` | Config values flowing into commands can be injection points |
| Python `repr()` used to build a JavaScript string | `settings_bridge.py` | Wrong escaping function could allow code injection |
| File path from the browser passed to a system command | `scribe_bridge.py`, `transcribe_bridge.py` | Browser-supplied paths could point anywhere |
| Install script writes a path into your shell config | `install.sh` | Shell config writes need careful quoting |

After the second-stage challenge, every one of these scored below 8. Here is why:

**The system commands do not use shell mode.** This is the most important point. When Python runs a system command as a list (e.g. `["open", "-a", app_name, file_path]`) rather than a plain string, the operating system receives each piece as a separate argument. Shell special characters like `;`, `|`, and `$()` have no effect. The injection risk simply does not exist.

**The browser cannot be reached by outside attackers.** The local browser window only loads files that are part of the app itself — there is no way for a remote attacker to inject JavaScript into it. The bridge between browser and Python is only reachable from the app's own code.

**The config file is owned by you.** An attacker who could modify `~/.config/liscribe/config.json` would already have full access to your account. There is no privilege to escalate to.

**The install script path comes from the script's own location.** It is never sourced from anything you type or any external input.

---

## Confidence scores explained

A high confidence score means "I am very sure this is a real problem." A low score means "this looked suspicious but turned out not to be dangerous."

In this review, **low confidence is a good outcome.** It means I looked carefully and could not find a way to exploit it.

| Finding | Confidence score | Meaning |
|---|---|---|
| App name from config to system command | 2 / 10 | Not exploitable — no shell mode, attacker must already own your account |
| `repr()` in JavaScript string | 2 / 10 | Not exploitable — anchor value is hardcoded internally, no external path in |
| File path from browser to system command | 3 / 10 | Not exploitable — no shell mode, path is app-generated not user-supplied |
| Install script shell quoting | 2 / 10 | Not exploitable — path comes from the script's own location on disk |

All four scored below the reporting threshold of 8. None are reported as vulnerabilities.

---

## Overall verdict

Liscribe is a local, offline, single-user tool. It runs no network server. It stores no passwords or API keys. It does not accept input from the internet. Its subprocess calls are written safely (no shell mode). Its config is loaded with the standard JSON parser, which cannot execute code.

**No exploitable vulnerabilities were found.**

The codebase follows the right patterns in the places that matter most. The things that looked suspicious on the surface all turned out to be safe on closer inspection.

---

## What this review does not cover

- Vulnerabilities introduced after this review was run
- Third-party dependencies (faster-whisper, pywebview, etc.) — those are tracked separately
- Physical access attacks or malware already running on your machine
