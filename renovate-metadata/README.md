# Renovate Metadata Action

This GitHub Action checks if a pull request is from Renovate and retrieves the update type.

## Inputs

- `skip-verification`: Skip commit signature verification (default: 'false')
- `renovate-actor`: GitHub username of the Renovate bot (default: 'kjoremiljo-renovate[bot]')

## Outputs

- `is-renovate`: Whether the PR is from Renovate
- `update-type`: Renovate update type (patch, minor, major, or unknown)

## Usage

```yaml
- uses: your-org/renovate-metadata@v1
  with:
    skip-verification: 'false'
    renovate-actor: 'kjoremiljo-renovate[bot]'
```

## Todo List

Here's a list of small atomic tasks that can improve the code:

1. Add error handling for the git commands in the "Get update type" step
2. Implement logging for better debugging and traceability
3. Add input validation for the `renovate-actor` parameter
4. Create a separate shell script for the "Get update type" logic to improve readability
5. Add a step to check if the PR title matches Renovate's format as an additional verification
6. Implement caching for the git operations to speed up the action
7. Add an option to customize the number of commits to search for Renovate updates
8. Implement a fallback mechanism if the commit signature verification fails
9. Add support for custom Renovate commit message formats
10. Create a changelog file to track changes to the action
