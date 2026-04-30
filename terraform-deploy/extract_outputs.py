#!/usr/bin/env python3
"""Extract non-sensitive Terraform outputs from `terraform output -json` stdout.

Terraform 1.15.0 may emit deprecation warnings to stdout (hashicorp/terraform#38484),
which contaminates the JSON. We use json.JSONDecoder.raw_decode, which parses one
complete JSON value and stops — trailing diagnostic text is ignored.

Reads from stdin, writes a flattened {name: value} JSON object to stdout, omitting
outputs marked sensitive.
"""

import json
import sys


def extract(raw: str) -> dict:
    start = raw.find("{")
    if start == -1:
        raise ValueError("no JSON object found in input")
    obj, _ = json.JSONDecoder().raw_decode(raw[start:])
    return {k: v["value"] for k, v in obj.items() if not v.get("sensitive", False)}


if __name__ == "__main__":
    print(json.dumps(extract(sys.stdin.read())))
