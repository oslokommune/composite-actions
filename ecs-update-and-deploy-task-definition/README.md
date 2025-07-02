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

## How to use

Here is an example deploying two containers in one task definition.

```yaml
    
    ecs-deploy:
      - runs-on: ubuntu-latest
        name: Deploy ECS task definition
        environment: pirates-dev-app-too-tikki-ecr
        permissions:
            id-token: write # For the GitHub's OIDC Token endpoint
        
        steps:

            -   name: Set images to deploy ⚙️
                uses: actions/github-script@60a0d83039c74a4aee543508d2ffcb1c3799cdea # v7.0.1
                id: set-images
                with:
                    script: |
                        // Images data structure documentation:
                        // https://github.com/oslokommune/composite-actions/blob/main/ecs-update-and-deploy-task-definition/README.md
                        const images = {};
                        
                        const initImageBuildResult = "${{ needs.docker-build-push-init.result }}";
                        const appImageBuildResult = "${{ needs.docker-build-push.result }}";
                        
                        console.log("Step result for init container image:", initImageBuildResult);
                        console.log("Step result for app container image:", appImageBuildResult);
                        
                        if (initImageBuildResult === 'success') {
                          images["init-container"] = {
                            "imageRepository": "pirates-dev-too-tikki-init",
                            "imageDigest": "${{ needs.docker-build-push-init.outputs.image_digest }}",
                            "imageTag": "${{ needs.docker-build-push-init.outputs.image_version }}"
                          };
                        }
                        
                        if (appImageBuildResult === 'success') {
                          images["too-tikki"] = {
                            "imageRepository": "pirates-dev-too-tikki",
                            "imageDigest": "${{ needs.docker-build-push.outputs.image_digest }}",
                            "imageTag": "${{ needs.docker-build-push.outputs.image_version }}"
                          };
                        }
                        
                        console.log("Images to deploy:");
                        console.log(images);
                        
                        return images;


            -   name: "Update and deploy ECS task definition with new image URI"
                uses: oslokommune/composite-actions/ecs-update-and-deploy-task-definition@... # set digest
                with:
                    aws-region: "eu-west-1"
                    aws-role-arn: "${{ secrets.AWS_ROLE_ARN }}"
                    
                    cluster-name: "pirates-dev"
                    service-name: "too-tikki"
                    task-definition-name: "too-tikki"
                    
                    deploy: "true"
                    wait-for-service-stability: "false"
                    
                    images: ${{ steps.set-images.outputs.result }}
                    images-ssm-parameter-name: "/pirates-dev/ecs/too-tikki/images"
```
