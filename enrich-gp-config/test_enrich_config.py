#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest"]
# ///
"""Tests for enrich_config.py."""

import copy
import json
from pathlib import Path

from enrich_config import enrich_config

TESTDATA = Path(__file__).parent / "testdata"


def load(name: str) -> dict:
    return json.loads((TESTDATA / name).read_text())


def test_enrich_full_config():
    result = enrich_config(load("full.json"), app_name="my-app", stack_name="app-example")
    assert result == load("full-expected.json")


def test_enrich_only_dev_config():
    result = enrich_config(load("only-dev.json"), app_name="my-app", stack_name="app-example")
    assert result == load("only-dev-expected.json")


def test_no_stack_name_skips_stack_dir():
    result = enrich_config(load("full.json"), app_name="my-app", stack_name="")
    assert "stackDir" not in result["dev"]
    assert "stackDir" not in result["prod"]


def test_no_app_name_skips_app_name_key():
    result = enrich_config(load("full.json"), app_name="", stack_name="app-example")
    assert "appName" not in result


def test_no_monorepo_uses_env_name_as_concurrency_group():
    config = load("full.json")
    del config["monorepo"]
    result = enrich_config(config, app_name="my-app", stack_name="")
    assert result["dev"]["concurrencyGroup"] == "pirates-dev"
    assert result["prod"]["concurrencyGroup"] == "pirates-prod"
