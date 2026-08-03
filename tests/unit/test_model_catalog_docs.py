# -*- coding: utf-8 -*-

"""Regression tests for dynamic model-catalog operational documentation."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OPERATIONAL_FILES = (
    ROOT / "README.md",
    ROOT / "AGENTS.md",
    ROOT / ".kiro" / "steering" / "setup.md",
    ROOT / ".kiro" / "skills" / "update-kiro-models" / "SKILL.md",
)


def test_operational_docs_do_not_reintroduce_static_catalogs() -> None:
    """Canonical guidance must keep Kiro discovery as the only catalog source."""
    forbidden = (
        "Fallback to static model list",
        "fallback to FALLBACK_MODELS",
        "edit `kiro/config.py`",
        '"claude-auto · 1x"',
        "To pin a default, set `ANTHROPIC_MODEL`",
        "nothing to update on the Claude Code side",
    )

    for path in OPERATIONAL_FILES:
        content = path.read_text(encoding="utf-8")
        for phrase in forbidden:
            assert phrase not in content, f"{phrase!r} found in {path.relative_to(ROOT)}"


def test_translated_readmes_do_not_publish_free_tier_snapshots() -> None:
    """Translated introductions point to discovery instead of copied model lists."""
    for path in sorted((ROOT / "docs").glob("*/README.md")):
        introduction = "\n".join(path.read_text(encoding="utf-8").splitlines()[:60])
        assert "Claude Sonnet 4.5" not in introduction, path.relative_to(ROOT)
        assert "sync_claude_models.py sync" in introduction, path.relative_to(ROOT)
