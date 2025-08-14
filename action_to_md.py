#!/usr/bin/env -S uv run --script

# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "click",
#     "pyyaml",
#     "pytablewriter",
# ]
# ///

"""
GitHub Actions Docs - Generate READMEs from GitHub Actions Composite action.yml files.

This tool parses an action.yml YAML GitHub Actions composite action config file and
converts it to a human-readable markdown document, similar to terraform-docs.
"""

import sys
import json
import yaml
import click
from typing import Any, Dict, List, Optional
from pathlib import Path
import pytablewriter as ptw


def load_yaml_file(file_path: Path) -> Dict[str, Any]:
    """Load and parse a YAML file."""
    try:
        with open(file_path, "r") as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"Error loading YAML file: {e}", file=sys.stderr)
        sys.exit(1)


def format_default(value: Any) -> str:
    """Format a default value for display."""
    if value is None:
        return "n/a"

    if isinstance(value, (dict, list)):
        # For complex types, create a JSON string representation
        # but limit to a single line for table compatibility
        return json.dumps(value)

    if isinstance(value, bool):
        return str(value).lower()

    return str(value)


def format_value(value: Any) -> str:
    """Format an output value for display."""
    if value is None:
        return "n/a"

    # Truncate long expressions for readability
    str_value = str(value)
    if len(str_value) > 60:
        return str_value[:57] + "..."

    return str_value


def is_required(input_config: Dict[str, Any]) -> str:
    """Determine if an input is required."""
    # GitHub Actions uses 'required' field explicitly
    required = input_config.get("required", False)

    # Handle both boolean and string representations
    if isinstance(required, bool):
        return "yes" if required else "no"
    elif isinstance(required, str):
        return "yes" if required.lower() in ["true", "yes"] else "no"

    return "no"


def generate_metadata_section(config: Dict[str, Any]) -> str:
    """Generate the metadata section with action name, description, and author."""
    markdown = ""

    name = config.get("name", "Unnamed Action")
    description = config.get("description", "")
    author = config.get("author", "")

    markdown += f"# {name}\n\n"

    if description:
        markdown += f"{description}\n\n"

    if author:
        markdown += f"**Author:** {author}\n\n"

    return markdown


def generate_inputs_table(inputs: Dict[str, Any]) -> str:
    """Generate a markdown table for action inputs using pytablewriter."""
    if not inputs:
        return "No inputs defined."

    # Prepare data for the table
    headers = ["Name", "Description", "Required", "Default"]
    rows = []

    for input_name, input_config in inputs.items():
        # Handle both dict and string formats
        if isinstance(input_config, dict):
            description = input_config.get("description", "")
            default = format_default(input_config.get("default"))
            required = is_required(input_config)
        else:
            # Sometimes inputs might be simple strings
            description = str(input_config)
            default = "n/a"
            required = "no"

        # Format the input name with backticks
        formatted_name = f"`{input_name}`"

        # Double backticks escape single backticks for default values
        rows.append(
            [formatted_name, description, required, f"``{default}``"]
        )

    # Create the table writer
    writer = ptw.MarkdownTableWriter(
        headers=headers,
        value_matrix=rows,
        align_list=["left", "left", "center", "left"],
    )

    # Get the table as a string
    return writer.dumps()


def generate_outputs_table(outputs: Dict[str, Any]) -> str:
    """Generate a markdown table for action outputs using pytablewriter."""
    if not outputs:
        return "No outputs defined."

    # Prepare data for the table
    headers = ["Name", "Description", "Value"]
    rows = []

    for output_name, output_config in outputs.items():
        # Handle both dict and string formats
        if isinstance(output_config, dict):
            description = output_config.get("description", "")
            value = format_value(output_config.get("value", ""))
        else:
            # Sometimes outputs might be simple strings
            description = ""
            value = str(output_config)

        # Format the output name with backticks
        formatted_name = f"`{output_name}`"

        # Double backticks escape single backticks for values
        rows.append(
            [formatted_name, description, f"``{value}``"]
        )

    # Create the table writer
    writer = ptw.MarkdownTableWriter(
        headers=headers,
        value_matrix=rows,
        align_list=["left", "left", "left"],
    )

    # Get the table as a string
    return writer.dumps()


def generate_usage_example(config: Dict[str, Any], config_file: Path) -> str:
    """Generate a usage example for the action."""
    markdown = "## Usage\n\n"

    # Determine the action path from the file location
    # Get the parent directory name (the action directory)
    action_dir = config_file.parent.name
    # Get the ref from config (passed from CLI option)
    ref = config.get("__ref", "main")
    # Use the correct GitHub syntax
    action_path = f"oslokommune/composite-actions/{action_dir}@{ref}"

    inputs = config.get("inputs", {})

    # Add inputs table if inputs exist
    if inputs:
        markdown += "### Inputs\n\n"

        # Create a table with name, description, required, and default
        headers = ["Input", "Description", "Required", "Default"]
        rows = []

        for input_name, input_config in inputs.items():
            if isinstance(input_config, dict):
                description = input_config.get("description", "")
                default = format_default(input_config.get("default"))
                required = is_required(input_config)
            else:
                description = str(input_config)
                default = "n/a"
                required = "no"

            rows.append([f"`{input_name}`", description, required, f"``{default}``"])

        writer = ptw.MarkdownTableWriter(
            headers=headers,
            value_matrix=rows,
            align_list=["left", "left", "center", "left"],
        )
        markdown += writer.dumps()
        markdown += "\n"

    # Add the usage example code block
    markdown += "### Example\n\n"
    markdown += "```yaml\n"
    markdown += "- name: " + config.get("name", "Use this action") + "\n"
    markdown += f"  uses: {action_path}\n"

    if inputs:
        markdown += "  with:\n"
        # Show all inputs with comments for required ones
        for input_name, input_config in inputs.items():
            if isinstance(input_config, dict):
                required = is_required(input_config) == "yes"
                default = input_config.get("default")

                if required:
                    markdown += f"    {input_name}: # Required\n"
                elif default is not None:
                    markdown += f"    # {input_name}: # Optional, default: {format_default(default)}\n"
                else:
                    markdown += f"    # {input_name}: # Optional\n"
            else:
                markdown += f"    # {input_name}: # Optional\n"

    markdown += "```\n\n"
    return markdown


def generate_markdown(config: Dict[str, Any], config_file: Path) -> str:
    """Generate markdown documentation from the action.yml config."""
    markdown = ""

    # Add command comment at the top
    markdown += f"<!-- Generated by running `make docs` from the project root -->\n\n"

    # Add metadata section (name, description, author)
    markdown += generate_metadata_section(config)

    # Check if this is a composite action
    runs = config.get("runs", {})
    if runs.get("using") != "composite":
        markdown += "**Note:** This action uses `" + runs.get("using", "unknown") + "` runtime.\n\n"

    # Add usage section with inputs and example (pass config_file now)
    markdown += generate_usage_example(config, config_file)

    # Add outputs section
    outputs = config.get("outputs", {})
    if outputs:
        markdown += "## Outputs\n\n"
        markdown += generate_outputs_table(outputs)
        markdown += "\n"

    # Add branding section if present
    branding = config.get("branding", {})
    if branding:
        markdown += "## Branding\n\n"
        icon = branding.get("icon", "")
        color = branding.get("color", "")
        if icon:
            markdown += f"- **Icon:** {icon}\n"
        if color:
            markdown += f"- **Color:** {color}\n"
        markdown += "\n"

    return markdown


@click.command()
@click.argument(
    "config_file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--ref",
    default="main",
    help="Git ref to use in the usage example (e.g., 'main', 'v1', 'master')",
)
def main(config_file: Path, ref: str):
    """Generate documentation from a GitHub Actions action.yml file."""
    # Load and parse the config file
    config = load_yaml_file(config_file)

    # Store the ref in config for use in generate_usage_example
    config["__ref"] = ref

    # Generate markdown
    markdown_content = generate_markdown(config, config_file)

    # Print to stdout for redirection
    print(markdown_content)


if __name__ == "__main__":
    main()
