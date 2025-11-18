#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

: "${AWSCREDS:?Missing AWSCREDS}"
: "${CONFIG:?Missing CONFIG}"
: "${SOURCE_TYPE:?Missing SOURCE_TYPE}"
: "${SOURCE_LOCATION:?Missing SOURCE_LOCATION}"
: "${TAG:?Missing TAG}"
: "${PARTIAL_WORKFLOW_DISPATCH_URL:?Missing PARTIAL_WORKFLOW_DISPATCH_URL}"
: "${GITHUB_WORKFLOW_REF:?Missing GITHUB_WORKFLOW_REF}"
: "${GITHUB_OUTPUT:?Missing GITHUB_OUTPUT}"
: "${GITHUB_STEP_SUMMARY:?Missing GITHUB_STEP_SUMMARY}"

source_type="$SOURCE_TYPE"
source_location="$SOURCE_LOCATION"
tag="$TAG"

aws_config_file="$(mktemp)"
package_tmp_dir=""

cleanup() {
  rm -f "$aws_config_file"
  if [[ -n "$package_tmp_dir" && -d "$package_tmp_dir" ]]; then
    rm -rf "$package_tmp_dir"
  fi
}
trap cleanup EXIT

log_info() {
  printf '[INFO] %s\n' "$*"
}

log_error() {
  printf '[ERROR] %s\n' "$*" >&2
}

die() {
  log_error "$1"
  exit 1
}

configure_aws_profiles() {
  printf '%s\n' "$AWSCREDS" >"$aws_config_file"
  export AWS_CONFIG_FILE="$aws_config_file"
}

package_folder_source_into_archive() {
  package_tmp_dir="$(mktemp -d)"
  (cd "$source_location" && zip -r "$package_tmp_dir/archive.zip" .)
  source_location="$package_tmp_dir/archive.zip"
  source_type="file"
}

append_file_extension_suffix() {
  local extension
  extension="$(printf '%s\n' "$source_location" | sed -n 's/^.*\.\(.*\)$/\1/p')"
  if [[ -n "$extension" ]]; then
    tag="$tag.$extension"
  fi
}

prepare_artifact_payload() {
  case "$source_type" in
    folder)
      log_info "Packaging folder artifact"
      package_folder_source_into_archive
      append_file_extension_suffix
      ;;
    file)
      log_info "Preparing file artifact"
      append_file_extension_suffix
      ;;
    docker-image)
      log_info "Preparing docker image artifact"
      ;;
    *)
      die "Unsupported source type: $source_type"
      ;;
  esac
}

upload_file_artifact() {
  local item="$1"
  local environment bucket_name

  environment="$(printf '%s' "$item" | jq -e -r .key)"
  bucket_name="$(printf '%s' "$item" | jq -e -r .value.artifactBucketName)"

  log_info "Uploading $source_location to S3 as $tag in $environment"

  export AWS_PROFILE="$environment"
  aws s3 cp "$source_location" "s3://$bucket_name/$tag"
}

upload_image_artifact() {
  local item="$1"
  local environment account_id ecr_repository_name default_region
  local login_password ecr_repository_uri image_tag

  environment="$(printf '%s' "$item" | jq -e -r .key)"
  account_id="$(printf '%s' "$item" | jq -e -r .value.accountId)"
  ecr_repository_name="$(printf '%s' "$item" | jq -e -r .value.artifactEcrRepositoryName)"
  default_region="$(printf '%s' "$item" | jq -e -r .value.defaultRegion)"

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

publish_artifact_to_environment() {
  local item="$1"

  case "$source_type" in
    file)
      upload_file_artifact "$item"
      ;;
    docker-image)
      upload_image_artifact "$item"
      ;;
    *)
      log_error "Unrecognized source type $source_type - skipping"
      ;;
  esac
}

deployment_environments() {
  printf '%s' "$CONFIG" | jq -c '{dev,prod} | to_entries | .[]'
}

publish_artifact_to_environments() {
  while read -r item; do
    publish_artifact_to_environment "$item"
  done < <(deployment_environments)
}

summarize_publication() {
  local workflow_filename workflow_dispatch_url
  workflow_filename="$(basename "${GITHUB_WORKFLOW_REF%%@*}")"
  workflow_dispatch_url="$PARTIAL_WORKFLOW_DISPATCH_URL/$workflow_filename"

  printf 'tag=%s\n' "$tag" >>"$GITHUB_OUTPUT"
  cat <<EOF >>"$GITHUB_STEP_SUMMARY"
Built and uploaded artifact with tag:
\`\`\`
$tag
\`\`\`

---

_To manually deploy the artifact, copy the tag and pass it in through a [workflow dispatch]($workflow_dispatch_url)_
EOF
}

main() {
  configure_aws_profiles
  prepare_artifact_payload
  publish_artifact_to_environments
  summarize_publication
}

main "$@"
