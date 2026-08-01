#!/bin/bash
# <bitbar.title>Kiro Credits</bitbar.title>
# <bitbar.version>v1.0</bitbar.version>
# <bitbar.desc>Show live Kiro subscription credit usage in the menu bar</bitbar.desc>
# <bitbar.dependencies>python3,kiro-gateway venv</bitbar.dependencies>
#
# SwiftBar plugin: Kiro credits usage (refreshes every 60s per filename).
# Lives in the repo at .kiro/skills/kiro-credits/swiftbar/kiro-credits.60s.sh
# and gets symlinked into the user's SwiftBar plugin folder by install.sh.

set -euo pipefail

# --- Resolve repo root from this script's real location (follows symlinks) ---
SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || realpath "${BASH_SOURCE[0]}")"
# Script lives at <repo>/.kiro/skills/kiro-credits/swiftbar/kiro-credits.60s.sh
REPO_ROOT="$(cd "$(dirname "$SCRIPT_PATH")/../../../.." && pwd)"

PYTHON="$REPO_ROOT/.venv/bin/python"
CHECK_SCRIPT="$REPO_ROOT/.kiro/skills/kiro-credits/check.py"

# --- Sanity checks ---
if [ ! -x "$PYTHON" ]; then
    echo "⚡️ --"
    echo "---"
    echo "venv not found | color=red"
    echo "Run: cd $REPO_ROOT && ./setup.sh | font=Menlo size=11"
    exit 0
fi

if [ ! -f "$CHECK_SCRIPT" ]; then
    echo "⚡️ --"
    echo "---"
    echo "check.py not found | color=red"
    exit 0
fi

# --- Fetch usage (15s timeout via perl; macOS lacks `timeout`) ---
if command -v gtimeout &>/dev/null; then
    output=$(gtimeout 15 "$PYTHON" "$CHECK_SCRIPT" 2>/dev/null)
else
    output=$(perl -e 'alarm 15; exec @ARGV' "$PYTHON" "$CHECK_SCRIPT" 2>/dev/null)
fi

if [ $? -ne 0 ] || [ -z "$output" ]; then
    echo "⚡️ --"
    echo "---"
    echo "Kiro credits: fetch failed | color=red"
    exit 0
fi

# --- Parse the Credits line: "  Credits:      62.87 / 5,000  (1.3%)" ---
credits_line=$(echo "$output" | grep -E "^\s+(Credits|Agentic requests)" | head -1)
used=$(echo "$credits_line" | grep -oE '[0-9][0-9,.]*' | head -1 | tr -d ',')
cap=$(echo "$credits_line" | grep -oE '[0-9][0-9,.]*' | sed -n '2p' | tr -d ',')
pct=$(echo "$credits_line" | grep -oE '[0-9.]+%' | head -1)

if [ -z "$used" ] || [ -z "$cap" ]; then
    echo "⚡️ --"
    echo "---"
    echo "Kiro credits: parse error | color=orange"
    echo "$output | font=Menlo size=11"
    exit 0
fi

# --- Format display values ---
used_display=$(printf "%.0f" "$used")
cap_display=$(printf "%'.0f" "$cap")

# --- Color based on usage ---
pct_num=$(echo "scale=0; $used * 100 / $cap" | bc 2>/dev/null || echo "0")
if [ "$pct_num" -ge 100 ]; then
    color="red"
elif [ "$pct_num" -ge 80 ]; then
    color="orange"
else
    color=""
fi

# --- Menu bar line ---
if [ -n "$color" ]; then
    echo "⚡️${used_display}/${cap_display} | color=$color"
else
    echo "⚡️${used_display}/${cap_display}"
fi

# --- Dropdown ---
echo "---"
echo "Kiro Credits (${pct:-n/a}) | size=14"
echo "$output" | while IFS= read -r line; do
    [ -n "$line" ] && echo "$line | font=Menlo size=11"
done
echo "---"
echo "Refresh | refresh=true"
