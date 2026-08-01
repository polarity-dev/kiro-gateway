#!/usr/bin/env bash
#
# Install the Kiro Credits SwiftBar widget.
#
# What it does:
#   1. Checks that SwiftBar is installed (offers to install via brew if not)
#   2. Detects the SwiftBar plugin directory
#   3. Symlinks kiro-credits.60s.sh into it
#   4. Optionally adds SwiftBar to login items
#
# Safe to re-run: replaces existing symlink if present.
# Pass -y/--yes for non-interactive mode.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_SRC="$SCRIPT_DIR/kiro-credits.60s.sh"

info()  { printf '  %s\n' "$*"; }
ok()    { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }
step()  { printf '\n\033[1m%s\033[0m\n' "$*"; }

ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    -y|--yes) ASSUME_YES=1 ;;
    -h|--help)
      printf 'Usage: %s [-y|--yes]\n\n  -y, --yes   Accept all prompts\n' "$0"
      exit 0 ;;
    *) fail "Unknown argument: $arg" ;;
  esac
done

ask() {
  if [ "$ASSUME_YES" -eq 1 ]; then return 0; fi
  printf '  %s [Y/n] ' "$1"
  read -r ans
  case "$ans" in
    ''|[Yy]*) return 0 ;;
    *) return 1 ;;
  esac
}

# --- Step 1: SwiftBar installed? ---
step "1/3  Checking SwiftBar"

if [ -d "/Applications/SwiftBar.app" ]; then
    ok "SwiftBar found at /Applications/SwiftBar.app"
elif command -v brew &>/dev/null; then
    warn "SwiftBar not found."
    if ask "Install SwiftBar via Homebrew?"; then
        brew install --cask swiftbar
        ok "SwiftBar installed"
    else
        fail "SwiftBar is required. Install it manually: brew install --cask swiftbar"
    fi
else
    fail "SwiftBar not found and Homebrew not available. Install SwiftBar from https://github.com/swiftbar/SwiftBar/releases"
fi

# --- Step 2: Find plugin directory ---
step "2/3  Locating plugin directory"

PLUGIN_DIR=""
# Check SwiftBar preferences
if command -v defaults &>/dev/null; then
    PLUGIN_DIR=$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null || true)
fi

# Fallback to common locations
if [ -z "$PLUGIN_DIR" ]; then
    for candidate in \
        "$HOME/Library/Application Support/SwiftBar/Plugins" \
        "$HOME/Documents/Swiftbar plugins" \
        "$HOME/.swiftbar"; do
        if [ -d "$candidate" ]; then
            PLUGIN_DIR="$candidate"
            break
        fi
    done
fi

# Still nothing — create default
if [ -z "$PLUGIN_DIR" ]; then
    PLUGIN_DIR="$HOME/Library/Application Support/SwiftBar/Plugins"
    info "No plugin directory found. Creating: $PLUGIN_DIR"
    mkdir -p "$PLUGIN_DIR"
    warn "You may need to set this folder in SwiftBar preferences on first launch."
fi

ok "Plugin directory: $PLUGIN_DIR"

# --- Step 3: Symlink the plugin ---
step "3/3  Installing widget"

DEST="$PLUGIN_DIR/kiro-credits.60s.sh"

if [ -L "$DEST" ]; then
    info "Replacing existing symlink"
    rm "$DEST"
elif [ -f "$DEST" ]; then
    warn "Existing file found (not a symlink). Backing up to ${DEST}.bak"
    mv "$DEST" "${DEST}.bak"
fi

chmod +x "$PLUGIN_SRC"
ln -s "$PLUGIN_SRC" "$DEST"
ok "Symlinked: $DEST → $PLUGIN_SRC"

# --- Optional: login items ---
if ! osascript -e 'tell application "System Events" to get the name of every login item' 2>/dev/null | grep -q "SwiftBar"; then
    if ask "Add SwiftBar to login items (auto-start on boot)?"; then
        osascript -e 'tell application "System Events" to make login item at end with properties {path:"/Applications/SwiftBar.app", hidden:false}' 2>/dev/null
        ok "SwiftBar added to login items"
    fi
fi

# --- Done ---
printf '\n\033[32m✓ Done!\033[0m Kiro credits widget installed.\n'
printf '  The menu bar will show ⚡️used/cap (updates every 60s).\n'
printf '  Launch SwiftBar if it is not already running: open -a SwiftBar\n\n'
