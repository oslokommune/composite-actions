---
name: composite-actions
description: |
  Oslo Kommune composite GitHub Actions for CI/CD pipelines. Includes actions for:
  AWS deployments (CloudFront, ECS, S3), Docker image operations (crane-copy-image),
  Terraform deployments, artifact packaging, release management, Renovate metadata,
  and CLI tool setup (ok, boilerplate, terraform). Use when building GitHub Actions
  workflows that need Oslo Kommune infrastructure patterns.
---

# Oslo Kommune Composite Actions

Reusable composite GitHub Actions for CI/CD pipelines at Oslo Kommune.

## Available Actions

| Action | Purpose |
|--------|---------|
| `cloudfront-deploy` | Deploy static sites to S3 + CloudFront |
| `crane-copy-image` | Copy container images between registries |
| `delete-release` | Delete a GitHub release |
| `detect-stale-job` | Prevent out-of-order deployments |
| `determine-stacks` | Determine which Terraform stacks to run |
| `disallow-same-approver` | Enforce four-eyes principle |
| `ecs-update-and-deploy-task-definition` | Deploy to ECS with updated images |
| `generate-tag` | Generate unique artifact tags |
| `optimize-apt-get` | Speed up apt-get on runners |
| `package-and-upload-artifact` | Upload artifacts to dev/prod AWS |
| `renovate-metadata` | Extract Renovate PR metadata |
| `repository-dispatch` | Trigger repository dispatch events |
| `setup-boilerplate` | Install Boilerplate CLI |
| `setup-ok` | Install ok CLI and dependencies |
| `terraform-deploy` | Deploy infrastructure via Terraform |
| `verify-created-release` | Verify release was created |

## Quick Reference

### Deployment Actions

**CloudFront Deploy** - Deploy static sites:
```yaml
- uses: oslokommune/composite-actions/cloudfront-deploy@v1
  with:
    aws-role-arn: ${{ secrets.AWS_ROLE_ARN }}
    s3-bucket-name: my-website-bucket
    site-path: ./dist
```

**ECS Deploy** - Update and deploy ECS task definitions:
```yaml
- uses: oslokommune/composite-actions/ecs-update-and-deploy-task-definition@v1
  with:
    aws-region: eu-west-1
    aws-role-arn: ${{ secrets.AWS_ROLE_ARN }}
    cluster-name: my-cluster
    service-name: my-service
    task-definition-name: my-task
    images: '[{"container": "app", "image": "123456789.dkr.ecr.eu-west-1.amazonaws.com/my-app:latest"}]'
    deploy: true
```

**Terraform Deploy** - Deploy infrastructure:
```yaml
- uses: oslokommune/composite-actions/terraform-deploy@v1
  with:
    config: ${{ needs.setup.outputs.config }}
    stack-dir: infrastructure/app
    environment: dev
    github-deploy-key: ${{ secrets.DEPLOY_KEY }}
```

### Setup Actions

**Setup ok CLI** - Install ok and dependencies:
```yaml
- uses: oslokommune/composite-actions/setup-ok@v1
  with:
    ok_version: latest
    terraform_version: 1.5.7
```

**Setup Boilerplate** - Install Boilerplate CLI:
```yaml
- uses: oslokommune/composite-actions/setup-boilerplate@v1
  with:
    version: 0.5.16
```

### Image Operations

**Copy Images** - Copy between ECR/GHCR:
```yaml
- uses: oslokommune/composite-actions/crane-copy-image@v1
  with:
    aws-region: eu-west-1
    source-image: ghcr.io/org/app:v1.0.0
    destination-image: 123456789.dkr.ecr.eu-west-1.amazonaws.com/app:v1.0.0
    aws-ecr-login: true
    ghcr-login: true
    token: ${{ secrets.GITHUB_TOKEN }}
```

### Safety Actions

**Detect Stale Job** - Prevent out-of-order deploys:
```yaml
- uses: oslokommune/composite-actions/detect-stale-job@v1
  with:
    cancel-if-stale: true
```

**Disallow Same Approver** - Four-eyes principle:
```yaml
- uses: oslokommune/composite-actions/disallow-same-approver@v1
```

## Read More

- [reference.md](./reference.md) - Complete input/output reference for all actions
- [examples.md](./examples.md) - Full workflow examples
