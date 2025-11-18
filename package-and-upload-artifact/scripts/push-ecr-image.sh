#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

: "${CONFIG:?Missing CONFIG}"
: "${SOURCE_TYPE:?Missing SOURCE_TYPE}"
: "${SOURCE_LOCATION:?Missing SOURCE_LOCATION}"
: "${TAG:?Missing TAG}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
source "$repo_root/lib/common.sh"

source_type="$SOURCE_TYPE"
source_location="$SOURCE_LOCATION"
tag="$TAG"

cleanup() {
  if [[ -n "${aws_config_file:-}" ]]; then
    rm -f "$aws_config_file"
  fi
}
trap cleanup EXIT

configure_aws

if [[ "$source_type" != "docker-image" ]]; then
  die "push-ecr-image.sh only supports docker-image sources (received $source_type)"
fi

upload_image_artifact() {
  local environment="$1"
  local account_id ecr_repository_name default_region
  local login_password ecr_repository_uri image_tag

  account_id="$(environment_value "$environment" accountId)"
  ecr_repository_name="$(environment_value "$environment" artifactEcrRepositoryName)"
  default_region="$(environment_value "$environment" defaultRegion)"

  export AWS_PROFILE="$environment"
  login_password="$(aws ecr get-login-password --region "$default_region")"
  printf '::add-mask::%s\n' "$login_password"

  ecr_repository_uri="$account_id.dkr.ecr.$default_region.amazonaws.com"
  image_tag="$ecr_repository_uri/$ecr_repository_name:$tag"

  printf '%s\n' "$login_password" | docker login --username AWS --password-stdin "$ecr_repository_uri"
  log_info "Tagging image with image tag: $image_tag"
  docker tag "$source_location" "$image_tag"
  log_info "Pushing image with tag: $image_tag"
  docker push "$image_tag"
}

for environment in dev prod; do
  if environment_defined "$environment"; then
    upload_image_artifact "$environment"
  fi
done

write_github_summary "$tag"
