# -*- coding: utf-8 -*-

"""Regression tests for unambiguous setup checkout instructions."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_canonical_runbook_uses_current_repo_automatically() -> None:
    """Normal setup uses the current repo without a checkout-selection step."""
    runbook = _read(".kiro/steering/setup.md")

    assert "Use the current repository directory" in runbook
    assert "do not ask them to choose one" in runbook
    assert "Only change directories when the user explicitly names" in runbook
    assert "Choose one checkout before doing anything" not in runbook
    assert "Using gateway checkout:" not in runbook


def test_skill_runs_every_step_in_current_repo() -> None:
    """The invocable skill keeps the normal flow in its current directory."""
    skill = _read(".kiro/skills/setup-gateway/SKILL.md")

    assert "run setup, startup, and verification in the current" in skill
    assert "do not ask them to choose a checkout" in skill
    assert "./setup.sh -y --aws-profile NAME --agent-events" in skill
    assert "python3 main.py" in skill
    assert "./setup.sh --check-port" in skill
    assert 'cd "PATH"' not in skill


def test_local_401_guidance_preserves_idc_credentials() -> None:
    """A proxy-key mismatch must not be treated as an AWS credential failure."""
    readme = _read("README.md")
    runbook = _read(".kiro/steering/setup.md")

    assert "Repeated `401 Invalid API key`" in readme
    assert "not an expired IAM Identity Center" in readme
    assert "token" in readme
    assert "Do not delete `kiro-gateway-auth.json`" in readme
    assert "do not delete IdC credentials" in runbook


def test_readme_has_no_checkout_selection_step() -> None:
    """The common clone-and-setup path remains direct and automatic."""
    readme = _read("README.md")

    assert "uses the current" in readme
    assert "repository directory automatically" in readme
    assert "there is no checkout-selection step" in readme
    assert "REPO_DIR=" not in readme
