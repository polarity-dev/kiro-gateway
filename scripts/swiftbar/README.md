# Kiro Credits — SwiftBar Widget

A macOS menu bar widget that shows your live Kiro subscription credit usage.
Updates every 60 seconds.

## What you see

```
⚡️70/5,000          ← menu bar (always visible)
─────────────────
Kiro Credits (1.4%)  ← click to expand
  Plan: KIRO PRO MAX
  Credits: 70.10 / 5,000  (1.4%)
  [------------------------------]
  Overage room: 10,000 available @ 0.04 USD/credit
  Resets: 2026-09-01  (30 days)
─────────────────
Refresh              ← force refresh
```

Color coding: default → normal, orange → 80%+ used, red → 100%+ (overage).

## Install

```bash
scripts/swiftbar/install.sh
```

Or non-interactively:

```bash
scripts/swiftbar/install.sh -y
```

The installer:
1. Checks/installs SwiftBar (`brew install --cask swiftbar`)
2. Finds your SwiftBar plugin folder
3. Symlinks the widget into it
4. Optionally adds SwiftBar to login items

It's also offered as an optional step during `./setup.sh`.

## Prerequisites

- macOS
- The kiro-gateway repo cloned and set up (`./setup.sh` already run)
- SwiftBar (installer handles this)

## How it works

The plugin is a symlink back into the repo. It resolves its own location
(following the symlink), finds the repo root, and runs
`.kiro/skills/kiro-credits/check.py` using the repo's `.venv/bin/python`.
No hardcoded paths — works from any clone location.

## Uninstall

```bash
rm "$(defaults read com.ameba.SwiftBar PluginDirectory)/kiro-credits.60s.sh"
```

## Changing the refresh interval

Rename the symlink — the interval is encoded in the filename:

```bash
cd "$(defaults read com.ameba.SwiftBar PluginDirectory)"
mv kiro-credits.60s.sh kiro-credits.5m.sh
```
