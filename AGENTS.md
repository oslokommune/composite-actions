# Agent instructions

## Git & Version Control

### Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org). Release Please releases the actions listed in
`.release-please-manifest.json`. Each has its own `CHANGELOG.md`, built from the commits that touch its folder, and
`feat`, `fix`, `deps` and `BREAKING CHANGE` headers are printed there verbatim. Write those headers as the sentence a
user should read there:

```text
type(scope): what changed, from the user's point of view
```

- Describe the outcome, not the mechanism.
- Scope is usually skipped. The changelog already belongs to one action, so the action name adds nothing.
  Add a scope only when an action has several parts a user tells apart, and name that part, not a file or script.
- Leave out "update", "wip", "fix bug", and ticket or PR numbers. Release Please appends the PR number itself.
- Test: reading only this line, does a user know whether it affects them?

Everything else in the repo (workflows, scripts, skills, docs) is not released and has no changelog, so the header
only has to make sense in `git log`. The type is still a Conventional Commits type, and the scope names the tool
(`renovate`, `ci`, `skills`).

Real headers from this repo, rewritten:

| Before                                                       | After                                                                         |
| ------------------------------------------------------------ | ----------------------------------------------------------------------------- |
| `fix(setup-ok): bump actions/cache to v5 for Node 24`        | `fix: stop the Node.js 20 deprecation warning when restoring the cache`       |
| `fix(terraform-deploy): unbreak deploys on Terraform 1.15.0` | `fix: deploys work again on Terraform 1.15.0`                                 |
| `feat: Support client-id param in terraform-deploy`          | `feat: accept client-id for GitHub App auth, replacing the deprecated app-id` |
