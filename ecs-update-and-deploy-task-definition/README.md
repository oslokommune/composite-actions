# Permissions

Permissions that must be granted to `GITHUB_TOKEN`:

```yml
permissions:
  id-token: write # For the GitHub's OIDC Token endpoint
```

## Images input data structure

```json
{
    "init-container": {
        "imageRepository": "pirates-dev-too-tikki-init",
        "imageDigest": "sha256:0d4626f3160ffcb561926074c0c3305a0faa7955",
        "imageTag": "2025-03-06_16-27-31_main_gha-13703418764_sha-b8a861e"
    },
    "too-tikki": {
        "imageRepository": "pirates-dev-too-tikki-main",
        "imageDigest": "sha256:c5777a8d16b664157b5ac56196f70527f1ce10e1",
        "imageTag": "2025-03-06_16-27-31_main_gha-13703418764_sha-b8a861e"
    }
}
```
