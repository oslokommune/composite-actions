#!/usr/bin/env -S uv run --script

#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "click",
# ]
# ///

import sys
import re
import click


@click.command()
@click.option("--section", "-s", default="CONTENT", help="Section name to look for in markers (default: 'CONTENT')")
@click.option("--start", help="Custom start marker text (overrides --section if provided)")
@click.option("--end", help="Custom end marker text (overrides --section if provided)")
@click.option("--source", "-S", type=click.File("r"), default="-", help="Source file for replacement content (default: stdin)")
@click.option("--target", "-t", type=click.File("r"), help="Target file to modify (required)")
@click.option("--output", "-o", type=click.File("w"), default="-", help="Output file (default: stdout)")
@click.option("--in-place", "-I", is_flag=True, help="Edit target file in-place")
@click.option("--dry-run", is_flag=True, help="Show what would be replaced without making changes")
@click.option("--discard-markers", is_flag=True, help="Discard the markers in the output")
@click.option("--regex", is_flag=True, help="Interpret custom markers as regular expressions")
@click.option("--create", "-c", is_flag=True, help="Create markers if they don't exist (appends to the file)")
@click.option("--create-position", type=click.Choice(["append", "prepend"]), default="append",
              help="Where to add new markers if --create is used (default: append)")
def replace_between(
    section, start, end, source, target, output, in_place, dry_run,
    discard_markers, regex, create, create_position
):
    """
    Replace text between specified markers with content from a file or stdin.

    By default, looks for content between <!-- CONTENT BEGIN --> and <!-- CONTENT END -->.
    Use --section to specify a different section name or provide explicit --start and --end markers.

    If markers don't exist in the target file, use --create to add them.

    Examples:

    # Replace content using default section markers:
    replace_between --source api_docs.md --target README.md

    # Replace content using a custom section name:
    replace_between --section API --source api_docs.md --target README.md

    # Using stdin as source and explicit markers:
    cat api_docs.md | replace_between --start "<!-- API START -->" --end "<!-- API END -->" --target README.md

    # In-place editing:
    replace_between --section API --source api_docs.md --target README.md --in-place

    # Create markers if they don't exist:
    replace_between --section API --source api_docs.md --target README.md --create
    """
    # Ensure we have required parameters
    if not target:
        raise click.UsageError("--target is required")

    # Determine start and end markers
    if start and end:
        # Use explicit markers if provided
        pass
    elif start or end:
        # If only one is provided, that's an error
        raise click.UsageError("Both --start and --end must be provided if using custom markers")
    else:
        # Use section markers
        start = f"<!-- {section} BEGIN -->"
        end = f"<!-- {section} END -->"

    # Handle in-place editing setup
    if in_place:
        output_name = target.name

    # Fetch target content
    target_content = target.read()

    # Fetch source content for replacement
    source_content = source.read()

    # Handle marker escaping for regex
    if not regex:
        escaped_start = re.escape(start)
        escaped_end = re.escape(end)
    else:
        escaped_start = start
        escaped_end = end

    # Create pattern for finding content between markers
    if discard_markers:
        pattern = f"{escaped_start}.*?{escaped_end}"
        repl = source_content
    else:
        pattern = f"({escaped_start}).*?({escaped_end})"
        repl = f"\\1\n{source_content}\n\\2"

    # Perform replacement
    result, count = re.subn(pattern, repl, target_content, flags=re.DOTALL)

    # Handle case when markers don't exist and --create is specified
    if count == 0:
        if create:
            # Create new content with markers
            if discard_markers:
                new_content = source_content
            else:
                new_content = f"{start}\n{source_content}\n{end}"

            # Add to existing content based on create_position
            if create_position == "append":
                if target_content and not target_content.endswith("\n"):
                    # Add a newline if the file doesn't end with one
                    result = target_content + "\n\n" + new_content
                else:
                    result = target_content + "\n" + new_content
                click.echo(f"Markers not found. Appending to target file.", err=True)
            else:  # prepend
                if target_content and not target_content.startswith("\n"):
                    # Add a newline if needed
                    result = new_content + "\n\n" + target_content
                else:
                    result = new_content + "\n" + target_content
                click.echo(f"Markers not found. Prepending to target file.", err=True)
        else:
            # No replacements made and --create not specified - error
            click.echo(f"Error: No markers matching '{start}' and '{end}' found in target file.", err=True)
            click.echo(f"Use --create to add markers if this is the first time.", err=True)
            sys.exit(1)

    # Display result for dry-run
    if dry_run:
        if count == 0 and create:
            click.echo("Would create new markers and add content.")
        else:
            click.echo(f"Would replace {count} occurrences of content between markers.")

        click.echo("Original content:")
        click.echo("---")
        click.echo(target_content)
        click.echo("---")
        click.echo("New content would be:")
        click.echo("---")
        click.echo(result)
        click.echo("---")
        return

    # Handle in-place editing
    if in_place:
        with open(output_name, 'w') as f:
            f.write(result)
        if count == 0 and create:
            click.echo(f"Created new markers in {output_name}", err=True)
        else:
            click.echo(f"Replaced {count} occurrences in {output_name}", err=True)
    else:
        # Write to output
        output.write(result)
        if output.name != "<stdout>":
            if count == 0 and create:
                click.echo(f"Created new markers in {output.name}", err=True)
            else:
                click.echo(f"Replaced {count} occurrences in {output.name}", err=True)


if __name__ == "__main__":
    replace_between()
