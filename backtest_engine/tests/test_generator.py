"""
Tests for strategy generator/schema: validation, distinctness, and diversity invariants.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path("/home/ml/projects/autonomous_quant_agent")
sys.path.insert(0, str(PROJECT_ROOT / "backtest_engine"))

from synthesis_layer.strategy_generator import generate_specs, MECHANISM_CLASSES  # noqa: E402
from synthesis_layer.strategy_schema import is_duplicate  # noqa: E402


def test_generator_produces_requested_count(tmp_path: Path):
    specs = generate_specs(5, tmp_path)
    assert len(specs) == 5
    assert all((tmp_path / f"spec_{s.spec_id}.json").exists() for s in specs)


def test_generated_specs_are_distinct():
    tmp = PROJECT_ROOT / "backtest_engine/tmp_test_specs"
    tmp.mkdir(exist_ok=True)
    specs = generate_specs(50, tmp)
    for i in range(len(specs)):
        for j in range(i + 1, len(specs)):
            assert not is_duplicate(specs[i], specs[j])


def test_generator_achieves_minimum_class_diversity():
    tmp = PROJECT_ROOT / "backtest_engine/tmp_test_specs"
    tmp.mkdir(exist_ok=True)
    specs = generate_specs(40, tmp)
    classes = {s.mechanism_class for s in specs}
    assert len(classes) >= 3
