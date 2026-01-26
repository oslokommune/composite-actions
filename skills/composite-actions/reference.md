# Composite Actions Reference

Complete input/output reference for all Oslo Kommune composite actions.

---

## cloudfront-deploy

Deploy pre-built static sites to S3 and CloudFront.

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `aws-role-arn` | Yes | - | AWS role ARN for OIDC authentication |
| `aws-region` | No | `eu-west-1` | AWS region |
| `s3-bucket-name` | Yes | - | S3 bucket name for deployment |
| `site-path` | No | `.` | Path to built site files |

### Behavior

1. Authenticates to AWS via OIDC
2. Finds CloudFront distribution by S3 bucket origin
3. Syncs non-HTML files first (with cache headers)
4. Syncs HTML files (no cache)
5. Creates CloudFront invalidation for `/*`

---

## crane-copy-image

Copy container images between registries using Crane.

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `aws-region` | Yes | - | AWS region for ECR |
| `aws-account-id` | No | - | AWS account ID (auto-detected if not set) |
| `ghcr-login` | No | `false` | Login to GitHub Container Registry |
| `token` | No | `${{ github.token }}` | GitHub token for GHCR |
| `aws-ecr-login` | No | `false` | Login to AWS ECR |
| `source-image` | Yes | - | Source image URI |
| `destination-image` | Yes | - | Destination image URI |

---

## delete-release

Delete a GitHub release if it exists.

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `release` | Yes | - | Release tag to delete |
| `token` | No | `${{ github.token }}` | GitHub token |

---

## detect-stale-job

Detect if a newer workflow run has already progressed further.

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `cancel-if-stale` | No | `true` | Cancel the job if stale |

### Outputs

| Output | Description |
|--------|-------------|
| `is-stale` | `true` if a newer run has progressed further |

---

## determine-stacks

Determine which Terraform stacks to run operations on.

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `selected-stacks` | No | - | Explicitly selected stacks |
| `ignored-stacks` | No | - | Stacks to ignore |
| `core-stacks` | No | - | Core infrastructure stacks |
| `override-core-stacks` | No | `false` | Override core stack detection |

### Outputs

| Output | Description |
|--------|-------------|
| `dev-core-stacks` | Core stacks for dev environment |
| `dev-apps-stacks` | Application stacks for dev |
| `prod-core-stacks` | Core stacks for production |
| `prod-apps-stacks` | Application stacks for production |
| `all-dev-stacks` | All dev stacks |
| `all-prod-stacks` | All production stacks |
| `all-stacks` | All stacks |

---

## disallow-same-approver

Prevent the person who initiated a deployment from approving it.

### Inputs

None.

### Behavior

Fails the job if the latest environment approver matches the actor who triggered the workflow.

---

## ecs-update-and-deploy-task-definition

Update ECS task definition with new container images and deploy.

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `aws-region` | Yes | - | AWS region |
| `aws-role-arn` | Yes | - | AWS role ARN for OIDC |
| `cluster-name` | Yes | - | ECS cluster name |
| `service-name` | Yes | - | ECS service name |
| `task-definition-name` | Yes | - | Task definition family name |
| `images` | Yes | - | JSON array of container/image pairs |
| `deploy` | Yes | - | Whether to deploy (`true`/`false`) |
| `wait-for-service-stability` | No | `true` | Wait for service to stabilize |
| `desired-count` | No | - | Desired task count |

### Outputs

| Output | Description |
|--------|-------------|
| `task-definition-file-name` | Path to downloaded task definition |

### Images Format

```json
[
  {"container": "app", "image": "123456789.dkr.ecr.eu-west-1.amazonaws.com/app:v1"},
  {"container": "sidecar", "image": "123456789.dkr.ecr.eu-west-1.amazonaws.com/sidecar:v1"}
]
```

---

## generate-tag

Generate a unique tag for artifacts.

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `identifier` | Yes | - | Base identifier for the tag |

### Outputs

| Output | Description |
|--------|-------------|
| `result` | Generated tag |

### Tag Format

- Default branch: `<identifier>-<timestamp>-<run_id>-<short_sha>-<branch>`
- Other branches: `xx-<identifier>-<timestamp>-<run_id>-<short_sha>-<branch>`

---

## optimize-apt-get

Disable unnecessary apt features to optimize installation performance.

### Inputs

None.

### Behavior

Disables:
- initramfs update
- man-db update
- fontconfig, install-info, mime, hicolor triggers

---

## package-and-upload-artifact

Package and upload artifacts to dev/prod AWS accounts.

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `config` | Yes | - | JSON config from `.gp.cicd.json` |
| `tag` | Yes | - | Artifact tag |
| `source-location` | Yes | - | Source path or image URI |
| `source-type` | Yes | - | `docker-image`, `file`, or `folder` |

### Outputs

| Output | Description |
|--------|-------------|
| `result` | Final tag value |

---

## renovate-metadata

Check if PR contains Renovate commits and extract metadata.

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `skip-verification` | No | `false` | Skip commit signature verification |
| `renovate-actor` | No | `renovate[bot]` | Expected Renovate actor |
| `fetch-depth` | No | `0` | Git fetch depth |

### Outputs

| Output | Description |
|--------|-------------|
| `is-renovate` | `true` if PR is from Renovate |
| `dependencies` | JSON string of updated dependencies |

---

## repository-dispatch

Create a repository dispatch event.

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `token` | No | `${{ github.token }}` | GitHub token |
| `repository` | No | `${{ github.repository }}` | Target repository |
| `event_type` | Yes | - | Event type name |
| `client_payload` | No | `{}` | JSON payload |

---

## setup-boilerplate

Install the Boilerplate CLI tool.

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `version` | No | `0.5.16` | Boilerplate version |

---

## setup-ok

Install the `ok` CLI and its dependencies.

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `ok_version` | No | (managed) | ok CLI version |
| `boilerplate_version` | No | (managed) | Boilerplate version |
| `terraform_version` | No | (managed) | Terraform version |
| `terragrunt_version` | No | (managed) | Terragrunt version |
| `yq_version` | No | (managed) | yq version |
| `tfswitch_version` | No | (managed) | tfswitch version |

All versions have Renovate-managed defaults. Use `latest` for latest version.

---

## terraform-deploy

Deploy application infrastructure through Terraform.

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `config` | Yes | - | JSON config from `.gp.cicd.json` |
| `stack-dir` | Yes | - | Terraform stack directory |
| `environment` | Yes | - | Target environment (`dev`/`prod`) |
| `tag` | No | - | Artifact tag |
| `target-repository` | No | - | Repository to checkout |
| `github-app-id` | No | - | GitHub App ID for auth |
| `github-app-private-key` | No | - | GitHub App private key |
| `github-deploy-key` | Yes | - | SSH deploy key |
| `send-deployment-event` | No | `false` | Send event to Datadog |
| `cancel-if-stale` | No | `true` | Cancel if stale job detected |

### Outputs

| Output | Description |
|--------|-------------|
| `terraform-outputs` | JSON-encoded Terraform outputs |

---

## verify-created-release

Verify that a release was created successfully.

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `release` | Yes | - | Release tag to verify |
| `token` | No | `${{ github.token }}` | GitHub token |

### Behavior

Fails if release doesn't exist or is still a draft.
