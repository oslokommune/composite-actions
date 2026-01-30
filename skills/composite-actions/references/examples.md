# Composite Actions Examples

Complete workflow examples using Oslo Kommune composite actions.

## Static Site Deployment to CloudFront

```yaml
name: Deploy Static Site

on:
  push:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: '20'

      - run: npm ci && npm run build

      - uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/

  deploy:
    needs: build
    runs-on: ubuntu-latest
    permissions:
      id-token: write
      contents: read
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/

      - uses: oslokommune/composite-actions/cloudfront-deploy@v1
        with:
          aws-role-arn: ${{ secrets.AWS_ROLE_ARN }}
          s3-bucket-name: my-website-bucket
          site-path: ./dist
```

## ECS Service Deployment

```yaml
name: Deploy to ECS

on:
  push:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest
    outputs:
      image: ${{ steps.build.outputs.image }}
    steps:
      - uses: actions/checkout@v4

      - id: build
        run: |
          IMAGE="123456789.dkr.ecr.eu-west-1.amazonaws.com/my-app:${{ github.sha }}"
          echo "image=$IMAGE" >> $GITHUB_OUTPUT
          # Build and push image...

  deploy-dev:
    needs: build
    runs-on: ubuntu-latest
    permissions:
      id-token: write
      contents: read
    steps:
      - uses: oslokommune/composite-actions/detect-stale-job@v1

      - uses: oslokommune/composite-actions/ecs-update-and-deploy-task-definition@v1
        with:
          aws-region: eu-west-1
          aws-role-arn: ${{ secrets.DEV_AWS_ROLE_ARN }}
          cluster-name: dev-cluster
          service-name: my-service
          task-definition-name: my-task
          images: '[{"container": "app", "image": "${{ needs.build.outputs.image }}"}]'
          deploy: true

  deploy-prod:
    needs: [build, deploy-dev]
    runs-on: ubuntu-latest
    environment: production
    permissions:
      id-token: write
      contents: read
    steps:
      - uses: oslokommune/composite-actions/detect-stale-job@v1

      - uses: oslokommune/composite-actions/disallow-same-approver@v1

      - uses: oslokommune/composite-actions/ecs-update-and-deploy-task-definition@v1
        with:
          aws-region: eu-west-1
          aws-role-arn: ${{ secrets.PROD_AWS_ROLE_ARN }}
          cluster-name: prod-cluster
          service-name: my-service
          task-definition-name: my-task
          images: '[{"container": "app", "image": "${{ needs.build.outputs.image }}"}]'
          deploy: true
          wait-for-service-stability: true
```

## Multi-Container ECS Deployment

```yaml
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: oslokommune/composite-actions/ecs-update-and-deploy-task-definition@v1
        with:
          aws-region: eu-west-1
          aws-role-arn: ${{ secrets.AWS_ROLE_ARN }}
          cluster-name: my-cluster
          service-name: my-service
          task-definition-name: my-task
          images: |
            [
              {"container": "api", "image": "${{ env.ECR_REGISTRY }}/api:${{ github.sha }}"},
              {"container": "worker", "image": "${{ env.ECR_REGISTRY }}/worker:${{ github.sha }}"},
              {"container": "nginx", "image": "${{ env.ECR_REGISTRY }}/nginx:${{ github.sha }}"}
            ]
          deploy: true
```

## Copy Image Between Registries

```yaml
name: Promote Image to Production

on:
  workflow_dispatch:
    inputs:
      tag:
        description: 'Image tag to promote'
        required: true

jobs:
  promote:
    runs-on: ubuntu-latest
    permissions:
      id-token: write
      contents: read
      packages: read
    steps:
      - uses: oslokommune/composite-actions/crane-copy-image@v1
        with:
          aws-region: eu-west-1
          source-image: ghcr.io/oslokommune/my-app:${{ inputs.tag }}
          destination-image: 123456789.dkr.ecr.eu-west-1.amazonaws.com/my-app:${{ inputs.tag }}
          ghcr-login: true
          aws-ecr-login: true
          token: ${{ secrets.GITHUB_TOKEN }}
```

## Terraform Infrastructure Deployment

```yaml
name: Deploy Infrastructure

on:
  push:
    branches: [main]
    paths:
      - 'infrastructure/**'

jobs:
  setup:
    runs-on: ubuntu-latest
    outputs:
      config: ${{ steps.config.outputs.result }}
      stacks: ${{ steps.stacks.outputs.all-stacks }}
    steps:
      - uses: actions/checkout@v4

      - id: config
        run: echo "result=$(cat .gp.cicd.json)" >> $GITHUB_OUTPUT

      - uses: oslokommune/composite-actions/determine-stacks@v1
        id: stacks

  deploy-dev:
    needs: setup
    runs-on: ubuntu-latest
    strategy:
      matrix:
        stack: ${{ fromJson(needs.setup.outputs.stacks) }}
    steps:
      - uses: oslokommune/composite-actions/setup-ok@v1

      - uses: oslokommune/composite-actions/terraform-deploy@v1
        with:
          config: ${{ needs.setup.outputs.config }}
          stack-dir: infrastructure/${{ matrix.stack }}
          environment: dev
          github-deploy-key: ${{ secrets.DEPLOY_KEY }}

  deploy-prod:
    needs: [setup, deploy-dev]
    runs-on: ubuntu-latest
    environment: production
    strategy:
      matrix:
        stack: ${{ fromJson(needs.setup.outputs.stacks) }}
    steps:
      - uses: oslokommune/composite-actions/disallow-same-approver@v1

      - uses: oslokommune/composite-actions/setup-ok@v1

      - uses: oslokommune/composite-actions/terraform-deploy@v1
        with:
          config: ${{ needs.setup.outputs.config }}
          stack-dir: infrastructure/${{ matrix.stack }}
          environment: prod
          github-deploy-key: ${{ secrets.DEPLOY_KEY }}
          send-deployment-event: true
```

## Release Management

```yaml
name: Release

on:
  push:
    tags:
      - 'v*'

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      # Delete existing release if re-running
      - uses: oslokommune/composite-actions/delete-release@v1
        with:
          release: ${{ github.ref_name }}
        continue-on-error: true

      - name: Create Release
        run: gh release create ${{ github.ref_name }} --generate-notes
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - uses: oslokommune/composite-actions/verify-created-release@v1
        with:
          release: ${{ github.ref_name }}
```

## Renovate Auto-Merge

```yaml
name: Renovate Auto-Merge

on:
  pull_request:
    types: [opened, synchronize]

jobs:
  check:
    runs-on: ubuntu-latest
    outputs:
      is-renovate: ${{ steps.meta.outputs.is-renovate }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: oslokommune/composite-actions/renovate-metadata@v1
        id: meta

  test:
    needs: check
    if: needs.check.outputs.is-renovate == 'true'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm ci && npm test

  auto-merge:
    needs: [check, test]
    if: needs.check.outputs.is-renovate == 'true'
    runs-on: ubuntu-latest
    steps:
      - run: gh pr merge --auto --squash ${{ github.event.pull_request.number }}
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

## Artifact Tagging and Upload

```yaml
name: Build and Upload

on:
  push:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest
    outputs:
      tag: ${{ steps.tag.outputs.result }}
    steps:
      - uses: actions/checkout@v4

      - uses: oslokommune/composite-actions/generate-tag@v1
        id: tag
        with:
          identifier: my-app

      - run: |
          echo "Generated tag: ${{ steps.tag.outputs.result }}"
          docker build -t my-app:${{ steps.tag.outputs.result }} .

      - uses: oslokommune/composite-actions/package-and-upload-artifact@v1
        with:
          config: ${{ needs.setup.outputs.config }}
          tag: ${{ steps.tag.outputs.result }}
          source-location: my-app:${{ steps.tag.outputs.result }}
          source-type: docker-image
```

## Optimized Runner Setup

```yaml
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      # Speed up apt installations
      - uses: oslokommune/composite-actions/optimize-apt-get@v1

      - run: |
          sudo apt-get update
          sudo apt-get install -y some-package
```

## Cross-Repository Dispatch

```yaml
name: Trigger Deployment

on:
  workflow_dispatch:

jobs:
  dispatch:
    runs-on: ubuntu-latest
    steps:
      - uses: oslokommune/composite-actions/repository-dispatch@v1
        with:
          token: ${{ secrets.PAT_TOKEN }}
          repository: oslokommune/deployment-repo
          event_type: deploy
          client_payload: '{"environment": "production", "version": "${{ github.sha }}"}'
```

## Setup ok CLI for Local Terraform

```yaml
jobs:
  terraform:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: oslokommune/composite-actions/setup-ok@v1
        with:
          ok_version: latest
          terraform_version: 1.6.0
          terragrunt_version: 0.54.0

      - run: |
          ok version
          terraform version
          terragrunt version
```
