#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# ///
"""Build a GP CI/CD config file with computed values (stackDir, concurrencyGroup, appName)."""

import argparse
import json


def build_config(config: dict, app_name: str, stack_name: str) -> dict:
    if app_name:
        config["appName"] = app_name

    for env in ("dev", "prod"):
        if env not in config:
            continue

        if stack_name:
            config[env]["stackDir"] = f"{config[env]['infrastructureRoot']}/{stack_name}"

        if config.get("monorepo"):
            config[env]["concurrencyGroup"] = f"{config[env]['name']}-{app_name}"
        else:
            config[env]["concurrencyGroup"] = config[env]["name"]

    return config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-file", required=True)
    parser.add_argument("--app-name", default="")
    parser.add_argument("--stack-name", default="")
    args = parser.parse_args()

    with open(args.config_file) as f:
        config = json.load(f)

    result = build_config(config, args.app_name, args.stack_name)
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
