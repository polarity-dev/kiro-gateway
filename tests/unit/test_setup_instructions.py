# -*- coding: utf-8 -*-

"""Regression tests for unambiguous setup-directory instructions."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_canonical_runbook_keeps_the_entire_flow_in_the_current_repo() -> None:
    """Normal setup uses one working directory unless another is explicitly named."""
    runbook = _read(".kiro/steering/setup.md")

    assert "Use the current repository directory" in runbook
    assert "Setup, gateway startup, port checks, and" in runbook
    assert "troubleshooting must keep using that same directory" in runbook
    assert "Only change directories" in runbook
    assert "explicitly names a different path" in runbook


def test_skill_runs_every_step_in_current_repo() -> None:
    """The invocable skill keeps the normal flow in its current directory."""
    skill = _read(".kiro/skills/setup-gateway/SKILL.md")

    assert "run setup, startup, and verification in the current" in skill
    assert "repository directory" in skill
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
    assert "return to the repository directory where setup was" in readme
    assert "run, rerun setup/alignment there" in readme
    assert "Do not delete `kiro-gateway-auth.json`" in readme
    assert "do not delete IdC credentials" in runbook


def test_readme_documents_automatic_directory_and_complete_custom_port_setup() -> None:
    """The common path remains automatic and the custom-port example is complete."""
    readme = _read("README.md")

    assert "uses the current" in readme
    assert "repository directory automatically" in readme
    assert "It uses another path only when you explicitly name one" in readme
    assert "./setup.sh -y --aws-profile NAME --port 9000" in readme
    assert "REPO_DIR=" not in readme
