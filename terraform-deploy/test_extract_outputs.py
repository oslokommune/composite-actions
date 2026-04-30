#!/usr/bin/env python3
"""Tests for extract_outputs.py. Run with: python3 test_extract_outputs.py"""

import json

from extract_outputs import extract


CLEAN = json.dumps(
    {
        "greeting": {"sensitive": False, "type": "string", "value": "hello"},
        "secret": {"sensitive": True, "type": "string", "value": "s3cret"},
        "count": {"sensitive": False, "type": "number", "value": 42},
        "tags": {
            "sensitive": False,
            "type": ["object", {"env": "string"}],
            "value": {"env": "dev"},
        },
    },
    indent=2,
)

# Mimics what `terraform output -json` produces in Terraform 1.15.0 when a
# deprecation warning slips onto stdout (see hashicorp/terraform#38484).
TRAILING_WARNING = (
    CLEAN
    + "\n╷\n│ Warning: Deprecated Parameter\n│ \n"
    "│ The parameter \"dynamodb_table\" is deprecated. Use parameter \"use_lockfile\" instead.\n"
    "╵\n"
)


def test_clean_output_drops_sensitive():
    assert extract(CLEAN) == {"greeting": "hello", "count": 42, "tags": {"env": "dev"}}


def test_trailing_warning_is_ignored():
    assert extract(TRAILING_WARNING) == {
        "greeting": "hello",
        "count": 42,
        "tags": {"env": "dev"},
    }


def test_all_sensitive_returns_empty():
    raw = json.dumps({"secret": {"sensitive": True, "type": "string", "value": "x"}})
    assert extract(raw) == {}


def test_no_outputs_returns_empty():
    assert extract("{}") == {}


def test_complex_value_shapes():
    # Lists, nested objects, and strings containing braces / stringified JSON
    # (a common pattern in Terraform: `jsonencode(...)` as an output value).
    raw = json.dumps(
        {
            "list": {
                "sensitive": False,
                "type": ["list", "string"],
                "value": ["a", "b", "c"],
            },
            "nested": {
                "sensitive": False,
                "type": ["object", {"a": ["object", {"b": "string"}]}],
                "value": {"a": {"b": "deep"}},
            },
            "stringified_json": {
                "sensitive": False,
                "type": "string",
                "value": '{"foo":"bar"}',
            },
            "tricky_chars": {
                "sensitive": False,
                "type": "string",
                "value": 'has } and { and "quotes"',
            },
        }
    )
    assert extract(raw) == {
        "list": ["a", "b", "c"],
        "nested": {"a": {"b": "deep"}},
        "stringified_json": '{"foo":"bar"}',
        "tricky_chars": 'has } and { and "quotes"',
    }


def test_no_json_raises():
    try:
        extract("just warnings, no json here")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
