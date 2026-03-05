#!/usr/bin/env bash
set -euo pipefail

# ── Liscribe installer ──────────────────────────────────────────────────────
# Usage: git clone <repo> && cd liscribe && ./install.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
ALIAS_MARKER="# liscribe"
MIN_PYTHON_MINOR=10

info()  { printf '\033[1;34m==> %s\033[0m\n' "$*"; }
warn()  { printf '\033[1;33m  ! %s\033[0m\n' "$*"; }
ok()    { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
fail()  { printf '\033[1;31m  ✗ %s\033[0m\n' "$*"; }
die()   { fail "$*"; exit 1; }

# ── 1. Prerequisites ────────────────────────────────────────────────────────

info "Checking prerequisites"

[[ "$(uname)" == "Darwin" ]] || die "Liscribe requires macOS."
ok "macOS detected"

PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 \
                 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    if command -v "$candidate" &>/dev/null; then
        minor="$("$candidate" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null)" || continue
        if (( minor >= MIN_PYTHON_MINOR )); then
            PYTHON="$(command -v "$candidate")"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    die "Python 3.$MIN_PYTHON_MINOR+ not found. Install with: brew install python@3.13"
fi
py_version="$("$PYTHON" -c 'import sys; print(sys.version_info.minor)')"
ok "Python 3.$py_version ($PYTHON)"

if ! command -v brew &>/dev/null; then
    die "Homebrew not found. Install from https://brew.sh"
fi
ok "Homebrew"

# ── 2. Brew dependencies (check only; do not install) ────────────────────────

info "Checking Homebrew dependencies"
echo "  (If you install missing deps with brew, it may print a lot of output; that's normal — wait for the next step.)"
printf '\n'
read -rp "  Enable speaker/system-audio capture? (requires BlackHole) [y/N] " speaker_yn

# Check required: portaudio. If speaker requested, also check blackhole-2ch and switchaudio-osx.
MISSING=()
MISSING_CASK=()

if ! brew list portaudio &>/dev/null; then
    MISSING+=(portaudio)
fi

if [[ "$speaker_yn" == [yY] ]]; then
    if ! brew list --cask blackhole-2ch &>/dev/null; then
        MISSING_CASK+=(blackhole-2ch)
    fi
    if ! brew list switchaudio-osx &>/dev/null; then
        MISSING+=(switchaudio-osx)
    fi
fi

# If any dependency is missing, print instructions and exit (do not run brew install).
if [[ ${#MISSING[@]} -gt 0 ]] || [[ ${#MISSING_CASK[@]} -gt 0 ]]; then
    echo ""
    fail "Some dependencies are missing."
    echo ""
    for pkg in "${MISSING[@]}"; do
        case "$pkg" in
            portaudio)
                echo "  You need portaudio for audio recording in liscribe."
                echo "  More info: https://formulae.brew.sh/formula/portaudio"
                ;;
            switchaudio-osx)
                echo "  You need switchaudio-osx to switch system output when using speaker capture (-s)."
                echo "  More info: https://formulae.brew.sh/formula/switchaudio-osx"
                ;;
            *)
                echo "  You need $pkg for liscribe."
                echo "  More info: https://formulae.brew.sh/formula/$pkg"
                ;;
        esac
        echo ""
    done
    for pkg in "${MISSING_CASK[@]}"; do
        case "$pkg" in
            blackhole-2ch)
                echo "  You need blackhole-2ch for system/speaker audio capture (-s) in liscribe."
                echo "  More info: https://existential.audio/blackhole/ or https://formulae.brew.sh/cask/blackhole-2ch"
                ;;
            *)
                echo "  You need $pkg for liscribe."
                echo "  More info: https://formulae.brew.sh/cask/$pkg"
                ;;
        esac
        echo ""
    done
    echo "  Install the missing items above (e.g. with Homebrew), then run ./install.sh again."
    echo ""
    read -rp "  Do you want to see install commands? [y/N] " show_cmds
    if [[ "$show_cmds" == [yY] ]]; then
        echo ""
        for pkg in "${MISSING[@]}"; do
            echo "  brew install $pkg"
        done
        for pkg in "${MISSING_CASK[@]}"; do
            echo "  brew install --cask $pkg"
        done
        echo ""
        if [[ ${#MISSING_CASK[@]} -gt 0 ]]; then
            echo "  Note: Cask installs (e.g. BlackHole) may request your password and a reboot."
            echo ""
        fi
    fi
    exit 1
fi

# All required deps present — report and continue (no brew install from this script).
if brew list portaudio &>/dev/null; then
    ok "portaudio already installed"
fi
if [[ "$speaker_yn" == [yY] ]]; then
    if brew list --cask blackhole-2ch &>/dev/null; then
        ok "blackhole-2ch already installed"
    fi
    if brew list switchaudio-osx &>/dev/null; then
        ok "switchaudio-osx already installed"
    fi
fi

# ── 3. Python venv & package ────────────────────────────────────────────────

info "Setting up Python environment"

if [[ -d "$VENV_DIR" ]]; then
    warn "Existing .venv found — recreating"
    rm -rf "$VENV_DIR"
fi

"$PYTHON" -m venv "$VENV_DIR"
ok "Virtual environment created"

printf '  Installing dependencies (this may take a minute)...'
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -e "$SCRIPT_DIR" --quiet
printf '\r'
ok "liscribe installed into venv                        "

# ── 4. Interactive configuration ─────────────────────────────────────────────

info "Configuration"

WHISPER_MODELS=("tiny" "base" "small" "medium" "large")
WHISPER_DESCS=(
    "~75 MB,  fastest, least accurate"
    "~150 MB, good balance for short recordings"
    "~500 MB, higher accuracy"
    "~1.5 GB, near-best accuracy, slower"
    "~3 GB,   best accuracy, slowest"
)

# Detect which models are already installed
already_installed=()
for i in "${!WHISPER_MODELS[@]}"; do
    model_name="${WHISPER_MODELS[$i]}"
    if "$VENV_DIR/bin/python" -c "from liscribe.transcriber import is_model_available; exit(0 if is_model_available('$model_name') else 1)" 2>/dev/null; then
        already_installed+=("$model_name")
    fi
done

echo ""
echo "  Available whisper models:"
for i in "${!WHISPER_MODELS[@]}"; do
    model_name="${WHISPER_MODELS[$i]}"
    installed_marker=""
    for m in "${already_installed[@]+"${already_installed[@]}"}"; do
        [[ "$m" == "$model_name" ]] && { installed_marker=" ✓ installed"; break; }
    done
    printf '    %d. %-8s %s%s\n' $((i+1)) "$model_name" "${WHISPER_DESCS[$i]}" "$installed_marker"
done
echo ""

# If models already installed, default is Enter to skip (use existing)
# If none installed, must pick at least one
have_installed=0
if (( ${#already_installed[@]} > 0 )); then
    have_installed=1
fi

sorted_indices=()
skip_models=0

if [[ $have_installed -eq 1 ]]; then
    echo "  You already have models installed. Press Enter to keep them, or pick numbers to download more."
    prompt_suffix=" (Enter to skip): "
else
    echo "  Enter numbers to download (e.g. 2,4,5 or 2-5 or all)"
    prompt_suffix=" (default: 2): "
fi

while true; do
    read -rp "  Models to download${prompt_suffix}" model_choice

    # Enter with no input + already have models → skip
    if [[ -z "$model_choice" && $have_installed -eq 1 ]]; then
        skip_models=1
        break
    fi

    # Default to base if no models installed and no input
    model_choice="${model_choice:-2}"

    # Parse "1,3,5", "1 3 5", "2-4", "all"
    indices=()
    if [[ "$model_choice" == "all" ]]; then
        for i in "${!WHISPER_MODELS[@]}"; do
            indices+=($((i+1)))
        done
    else
        cleaned="${model_choice//,/ }"
        for part in $cleaned; do
            if [[ "$part" =~ ^([0-9]+)-([0-9]+)$ ]]; then
                start="${BASH_REMATCH[1]}"
                end="${BASH_REMATCH[2]}"
                for ((i=start; i<=end; i++)); do
                    if (( i >= 1 && i <= ${#WHISPER_MODELS[@]} )); then
                        indices+=($i)
                    fi
                done
            elif [[ "$part" =~ ^[0-9]+$ ]]; then
                num=$((part))
                if (( num >= 1 && num <= ${#WHISPER_MODELS[@]} )); then
                    indices+=($num)
                fi
            fi
        done
    fi

    if (( ${#indices[@]} > 0 )); then
        unique_indices=()
        for idx in "${indices[@]}"; do
            found=0
            if (( ${#unique_indices[@]} > 0 )); then
                for u in "${unique_indices[@]}"; do
                    [[ "$u" == "$idx" ]] && { found=1; break; }
                done
            fi
            [[ $found -eq 0 ]] && unique_indices+=($idx)
        done
        if (( ${#unique_indices[@]} > 0 )); then
            IFS=$'\n' sorted_indices=($(sort -n <<<"${unique_indices[*]}"))
            unset IFS
        fi
        if (( ${#sorted_indices[@]} > 0 )); then
            break
        fi
    fi

    warn "Enter numbers (e.g. 2,4,5 or 2-5 or all)"
done

# Build to_download list
to_download=()
for idx in "${sorted_indices[@]+"${sorted_indices[@]}"}"; do
    to_download+=("${WHISPER_MODELS[$((idx-1))]}")
done

# Determine chosen_model (default for config)
if [[ $skip_models -eq 1 ]]; then
    # Use the best already-installed model as default (Bash 3.x-safe: no negative subscript)
    chosen_model="${already_installed[${#already_installed[@]}-1]}"
    # Prefer base if installed
    for m in "${already_installed[@]}"; do
        [[ "$m" == "base" ]] && { chosen_model="base"; break; }
    done
    ok "Keeping existing models (${already_installed[*]})"
else
    # Use first selected model as default (or base if in list)
    chosen_model="${WHISPER_MODELS[$((sorted_indices[0]-1))]}"
    for idx in "${sorted_indices[@]}"; do
        [[ "${WHISPER_MODELS[$((idx-1))]}" == "base" ]] && { chosen_model="base"; break; }
    done
    if (( ${#to_download[@]} == 1 )); then
        ok "Model: ${to_download[0]}"
    else
        ok "Models: ${to_download[*]}"
    fi
fi

echo ""
read -rp "  Transcription language (ISO 639-1 code, e.g. en, fr, de, or 'auto') [en]: " chosen_lang
chosen_lang="${chosen_lang:-en}"
chosen_lang="$(echo "$chosen_lang" | tr '[:upper:]' '[:lower:]')"
ok "Language: $chosen_lang"

LISCRIBE_WHISPER_MODEL="$chosen_model" LISCRIBE_LANGUAGE="$chosen_lang" \
  "$VENV_DIR/bin/python" - <<'EOF'
import os
from liscribe.config import load_config, save_config, init_config_if_missing
init_config_if_missing()
cfg = load_config()
cfg['whisper_model'] = os.environ['LISCRIBE_WHISPER_MODEL']
cfg['language'] = os.environ['LISCRIBE_LANGUAGE']
save_config(cfg)
EOF
ok "Config saved to ~/.config/liscribe/config.json"

# ── 5. Shell alias ──────────────────────────────────────────────────────────

info "Shell alias setup"

detect_shell_rc() {
    local sh
    sh="$(basename "${SHELL:-/bin/zsh}")"
    case "$sh" in
        zsh)  echo "$HOME/.zshrc" ;;
        bash) echo "$HOME/.bashrc" ;;
        *)    echo "$HOME/.${sh}rc" ;;
    esac
}

SHELL_RC="$(detect_shell_rc)"
REC_BIN="$VENV_DIR/bin/rec"

echo ""
read -rp "  Alias name (default: rec): " alias_name
alias_name="${alias_name:-rec}"
if [[ ! "$alias_name" =~ ^[a-zA-Z0-9_-]+$ ]]; then
    warn "Alias name contains invalid characters. Using 'rec' instead."
    alias_name="rec"
fi

ALIAS_LINE="alias ${alias_name}='${REC_BIN}'  ${ALIAS_MARKER}"

if [[ -f "$SHELL_RC" ]]; then
    existing="$(grep "$ALIAS_MARKER" "$SHELL_RC" 2>/dev/null || true)"
    if [[ -n "$existing" ]]; then
        warn "Found existing liscribe alias in $SHELL_RC:"
        echo "    $existing"
        read -rp "  Remove and replace it? [Y/n] " replace_yn
        if [[ "$replace_yn" != [nN] ]]; then
            sed_backup=".liscribe-bak"
            sed -i"$sed_backup" "/$ALIAS_MARKER/d" "$SHELL_RC"
            rm -f "${SHELL_RC}${sed_backup}"
            ok "Removed old alias"
        else
            warn "Keeping existing alias — skipping"
            alias_name=""
        fi
    fi
fi

if [[ -n "$alias_name" ]]; then
    touch "$SHELL_RC"
    printf '\n%s\n' "$ALIAS_LINE" >> "$SHELL_RC"
    ok "Added to $SHELL_RC: $ALIAS_LINE"
fi

# ── 6. Dictation setup ───────────────────────────────────────────────────────

info "Dictation"

echo ""
echo "  Liscribe has two modes:"
echo ""
echo "    Recording  — open the app, record, get a transcript file saved to disk"
echo "    Dictation  — double-tap a key anywhere on your Mac, speak, and the"
echo "                 text is typed for you automatically in whatever app is open"
echo ""
echo "  Dictation runs silently in the background — no terminal window needed."
echo ""
read -rp "  Enable dictation (runs at login, works everywhere)? [y/N] " dictation_yn

if [[ "$dictation_yn" == [yY] ]]; then
    echo ""
    echo "  Installing dictation daemon..."
    "$VENV_DIR/bin/rec" dictate install >/dev/null 2>&1 || true
    ok "Dictation daemon installed (login item created)"

    # The daemon runs as the Python binary in the venv — macOS tracks permissions
    # per-binary. We need to grant Input Monitoring + Accessibility to that binary.
    PYTHON_BIN="$(cd "$VENV_DIR/bin" && pwd)/python3"
    if [[ ! -f "$PYTHON_BIN" ]]; then
        PYTHON_BIN="$VENV_DIR/bin/python"
    fi
    # Resolve symlink to real binary (macOS TCC checks the resolved path)
    PYTHON_REAL="$(python3 -c "import os,sys; print(os.path.realpath('$PYTHON_BIN'))" 2>/dev/null || echo "$PYTHON_BIN")"

    echo ""
    warn "Two permissions are required for dictation to work."
    echo ""
    echo "  The dictation daemon runs as:"
    echo "    $PYTHON_REAL"
    echo ""
    echo "  macOS must allow that binary to monitor keys and paste text."
    echo ""
    echo "  ── Step 1 of 2: Accessibility ──────────────────────────────────────────"
    echo ""
    echo "  Opening System Settings → Privacy & Security → Accessibility."
    echo "  Click + , press Cmd+Shift+G, paste the path above, and click Open."
    echo ""
    open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" 2>/dev/null || true
    echo ""
    read -rp "  Press Enter once you have added the binary to Accessibility: " _
    echo ""
    echo "  ── Step 2 of 2: Input Monitoring ───────────────────────────────────────"
    echo ""
    echo "  Opening System Settings → Privacy & Security → Input Monitoring."
    echo "  Click + , press Cmd+Shift+G, paste the path above, and click Open."
    echo ""
    open "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent" 2>/dev/null || true
    echo ""
    read -rp "  Press Enter once you have added the binary to Input Monitoring: " _

    # Restart daemon so it picks up the new permissions
    "$VENV_DIR/bin/rec" dictate install >/dev/null 2>&1 || true
    ok "Dictation daemon restarted"

    echo ""
    ok "Dictation is active. Now test it:"
    echo ""
    echo "    Double-tap Right Option (⌥) anywhere on your Mac and speak."
    echo "    A small overlay appears near your cursor while recording."
    echo "    Tap Right Option (⌥) once to stop — your text is pasted instantly."
    echo ""
    echo "  Change the hotkey anytime in: rec preferences → Dictation"
else
    ok "Dictation skipped — enable anytime in: rec preferences → Dictation"
fi

# ── 7. Download whisper models ────────────────────────────────────────────────

info "Whisper models"

if [[ $skip_models -eq 1 ]] || (( ${#to_download[@]} == 0 )); then
    warn "No models selected — skipping download"
else
    models_str="${to_download[*]}"
    models_str="${models_str// /, }"
    echo ""
    read -rp "  Download ${#to_download[@]} model(s) now? ($models_str) [Y/n] " dl_yn
    if [[ "$dl_yn" != [nN] ]]; then
        for model_name in "${to_download[@]}"; do
            # Check if already installed
            if "$VENV_DIR/bin/python" -c "from liscribe.transcriber import is_model_available; exit(0 if is_model_available('$model_name') else 1)" 2>/dev/null; then
                echo "  Skipping '$model_name' (already installed)"
                continue
            fi
            echo "  Downloading '$model_name' (this may take a moment)..."
            if "$VENV_DIR/bin/python" -c "
from liscribe.transcriber import load_model
load_model('$model_name')
" 2>&1; then
                ok "Model '$model_name' ready"
            else
                warn "Failed to download '$model_name'"
            fi
        done
    else
        warn "Skipped — models will download on first use"
    fi
fi

# ── 8. Done ──────────────────────────────────────────────────────────────────

echo ""
info "Installation complete!"
echo ""
echo "  To start using liscribe, either:"
echo "    1. Open a new terminal, or"
echo "    2. Run: source $SHELL_RC"
echo ""
echo "  Then:"
echo "    ${alias_name:-rec} -f ~/transcripts              # record mic"
echo "    ${alias_name:-rec} -f ~/transcripts -s           # record mic + speaker"
echo "    ${alias_name:-rec} setup                         # add/change models or language"
echo "    ${alias_name:-rec} devices                       # list audio devices"
echo ""
echo "  Use -xxs -xs -sm -md -lg to transcribe with multiple models (e.g. ${alias_name:-rec} -h -xxs -sm)."
echo "  Run ${alias_name:-rec} setup to download any models you did not install now."
echo ""
