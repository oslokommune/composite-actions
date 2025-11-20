#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

: "${CONFIG:?Missing CONFIG}"
: "${IMAGE_ID:?Missing IMAGE_ID}"
: "${TAG:?Missing TAG}"
: "${PARTIAL_WORKFLOW_DISPATCH_URL:?Missing PARTIAL_WORKFLOW_DISPATCH_URL}"
: "${GITHUB_WORKFLOW_REF:?Missing GITHUB_WORKFLOW_REF}"
: "${GITHUB_OUTPUT:?Missing GITHUB_OUTPUT}"
: "${GITHUB_STEP_SUMMARY:?Missing GITHUB_STEP_SUMMARY}"
: "${GITHUB_ENV:?Missing GITHUB_ENV}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
action_dir="$(cd "$script_dir/.." && pwd)"

# Import common functions from configure-aws-credentials action
configure_action_dir="$(cd "$action_dir/../configure-aws-credentials" && pwd)"
source "$configure_action_dir/lib/common.sh"

require_cmd docker

image_id="$IMAGE_ID"
tag="$TAG"

push_image_to_ecr() {
  local environment="$1"
  local account_id ecr_repository_name default_region
  local login_password ecr_repository_uri image_tag

  account_id="$(environment_value "$environment" accountId)"
  ecr_repository_name="$(environment_value "$environment" artifactEcrRepositoryName)"
  default_region="$(environment_value "$environment" defaultRegion)"

  login_password="$(aws ecr get-login-password --region "$default_region")"
  printf '::add-mask::%s\n' "$login_password"

  ecr_repository_uri="$account_id.dkr.ecr.$default_region.amazonaws.com"
  image_tag="$ecr_repository_uri/$ecr_repository_name:$tag"

  printf '%s\n' "$login_password" | docker login --username AWS --password-stdin "$ecr_repository_uri"
  log_info "Tagging image with image tag: $image_tag"
  docker tag "$image_id" "$image_tag"
  log_info "Pushing image with tag: $image_tag"
  docker push "$image_tag"
  docker logout "$ecr_repository_uri"
}

environments="$(printf '%s' "$CONFIG" | jq -r 'to_entries[] | select(.value.artifactRoleArn != null) | .key')"
if [[ -z "$environments" ]]; then
  die "No environments with artifactRoleArn defined in config"
fi

for environment in $environments; do
  if ! environment_defined "$environment"; then
    continue
  fi

  role_arn="$(environment_value "$environment" artifactRoleArn)"
  default_region="$(environment_value "$environment" defaultRegion)"

  authenticate_via_oidc "$role_arn" "$default_region"
  export AWS_REGION="$default_region"
  export AWS_DEFAULT_REGION="$default_region"

  push_image_to_ecr "$environment"
  clear_credentials
done

# Write summary and outputs
workflow_filename="$(basename "${GITHUB_WORKFLOW_REF%%@*}")"
workflow_dispatch_url="$PARTIAL_WORKFLOW_DISPATCH_URL/$workflow_filename"

printf 'ARTIFACT_TAG=%s\n' "$tag" >>"$GITHUB_ENV"
cat <<EOF >>"$GITHUB_STEP_SUMMARY"
Built and uploaded Docker image with tag:
\`\`\`
$tag
\`\`\`

---

_To manually deploy the artifact, copy the tag and pass it in through a [workflow dispatch]($workflow_dispatch_url)_
EOF
