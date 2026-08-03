# -*- coding: utf-8 -*-

"""Tests for the Kiro runtime model probe CLI."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from kiro.config import FALLBACK_MODELS


@pytest.fixture(scope="module")
def probe_models_module() -> ModuleType:
    """Load scripts/probe_models.py as an importable module.

    Returns:
        Loaded probe module without invoking its CLI entry point.
    """
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "probe_models.py"
    spec = importlib.util.spec_from_file_location("probe_models_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load probe script from {script_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_static_candidates_include_every_fallback_model(
    probe_models_module: ModuleType,
) -> None:
    """Ensure removed fallback models remain visible to regression probes."""
    fallback_ids = {model["modelId"] for model in FALLBACK_MODELS}

    assert set(probe_models_module._STATIC_CANDIDATES) == fallback_ids


@pytest.mark.parametrize("no_discover", [False, True])
def test_explicit_models_always_skip_discovery(
    probe_models_module: ModuleType,
    no_discover: bool,
) -> None:
    """Ensure a targeted probe never sends requests for the full catalog."""
    candidates, discover = probe_models_module._select_candidates(
        ["candidate-a", "candidate-b"],
        no_discover,
    )

    assert candidates == ["candidate-a", "candidate-b"]
    assert discover is False


def test_default_probe_discovers_and_audits_fallbacks(
    probe_models_module: ModuleType,
) -> None:
    """Ensure the normal CLI path combines discovery with fallback auditing."""
    candidates, discover = probe_models_module._select_candidates(None, False)

    assert candidates == probe_models_module._STATIC_CANDIDATES
    assert discover is True


def test_no_discover_uses_only_static_fallbacks(
    probe_models_module: ModuleType,
) -> None:
    """Ensure offline audits use the complete configured fallback set."""
    candidates, discover = probe_models_module._select_candidates(None, True)

    assert candidates == probe_models_module._STATIC_CANDIDATES
    assert discover is False
