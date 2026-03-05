# Dictation Setup Guide

## Step 1: Run `rec dictate` once to trigger permission prompts

macOS won't ask for permissions until the app actually tries to use them. Run:

    rec dictate

and attempt a double-tap. You will see two system permission dialogs:

### Input Monitoring

Required for `rec dictate` to detect global key presses from any app.

1. macOS shows: "Terminal wants to monitor input from your keyboard"
2. Click **Open System Settings**
3. Go to **Privacy & Security → Input Monitoring**
4. Toggle your terminal app (Terminal, iTerm2, Warp, etc.) **on**

### Accessibility

Required to simulate Cmd+V to paste the transcript at the cursor.

1. macOS shows: "Terminal wants access to control this computer"
2. Click **Open System Settings**
3. Go to **Privacy & Security → Accessibility**
4. Toggle your terminal app **on**

After granting both, re-run `rec dictate`. You should not see the prompts again.

---

> **Tip:** If you granted permission but `rec dictate` still can't listen, try removing
> the entry from Input Monitoring and re-adding it. macOS occasionally gets the permission
> state out of sync after updates.

---

## Step 2 (optional): Auto-start on login with a LaunchAgent

A LaunchAgent runs `rec dictate` automatically when you log in, so dictation is always
available without opening a terminal.

### Install the login item

    rec dictate install

This writes:
- `~/Library/LaunchAgents/com.liscribe.dictate.plist`
- `~/.local/share/liscribe/dictate.log`

### Verify status

    rec dictate status

### Check logs

    tail -f ~/.local/share/liscribe/dictate.log

### Permanently remove auto-start

    rec dictate uninstall

---

> **Note on sounds with LaunchAgent:** When running as a LaunchAgent, `rec dictate` has
> no terminal window. Use `--no-sounds` if you find the sounds play unexpectedly, or leave
> them on — macOS notifications still appear regardless.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "Could not start keyboard listener" on first run | Grant Input Monitoring in System Settings |
| Paste does nothing (text not appearing) | Grant Accessibility in System Settings |
| Double-tap not detected | Check the hotkey matches your config: `rec config --show` |
| Wrong hotkey | Change in **Preferences → Dictation**, or `rec dictate --hotkey right_ctrl` |
| No sound but notifications appear | Expected if `afplay` is unavailable; sounds need macOS built-in `afplay` |
| LaunchAgent won't start | Run `rec dictate status`; check error log at `~/.local/share/liscribe/dictate.log` |
| Permission granted but still failing | Remove from Input Monitoring, re-add, then re-run |
