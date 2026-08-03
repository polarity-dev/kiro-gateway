# -*- coding: utf-8 -*-

"""Tests for the Kiro runtime model probe CLI."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture(scope="module")
def probe_models_module() -> ModuleType:
    """Load scripts/probe_models.py without invoking its CLI entry point."""
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "probe_models.py"
    spec = importlib.util.spec_from_file_location("probe_models_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load probe script from {script_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_explicit_models_skip_discovery(probe_models_module: ModuleType) -> None:
    """Ensure targeted probes never fan out into catalog requests."""
    candidates, discover = probe_models_module._select_candidates(
        ["candidate-a", "candidate-b"],
        False,
    )

    assert candidates == ["candidate-a", "candidate-b"]
    assert discover is False


def test_default_probe_uses_live_discovery(probe_models_module: ModuleType) -> None:
    """Ensure the default probe has no static model candidates."""
    candidates, discover = probe_models_module._select_candidates(None, False)

    assert candidates == []
    assert discover is True
