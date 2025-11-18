#!/usr/bin/env bash
set -euo pipefail

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

cleanup() {
  rm -f "$aws_config_file"
}
trap cleanup EXIT

configure_aws_credentials() {
  printf '%s\n' "$AWSCREDS" >"$aws_config_file"
  export AWS_CONFIG_FILE="$aws_config_file"
}

package_folder_source() {
  (cd "$source_location" && zip -r ../archive.zip .)
  source_location="archive.zip"
  source_type="file"
}

append_extension_to_tag() {
  local extension
  extension="$(printf '%s\n' "$source_location" | sed -n 's/^.*\.\(.*\)$/\1/p')"
  if [[ -n "$extension" ]]; then
    tag="$tag.$extension"
  fi
}

package_artifact() {
  if [[ "$source_type" == "folder" ]]; then
    package_folder_source
  fi

  if [[ "$source_type" == "file" ]]; then
    append_extension_to_tag
  fi
}

upload_file_to_s3() {
  local item="$1"
  local environment bucket_name

  environment="$(printf '%s' "$item" | jq -e -r .key)"
  bucket_name="$(printf '%s' "$item" | jq -e -r .value.artifactBucketName)"

  printf 'Uploading %s to S3 with key %s in %s\n' "$source_location" "$tag" "$environment"

  export AWS_PROFILE="$environment"
  aws s3 cp "$source_location" "s3://$bucket_name/$tag"
}

push_image_to_ecr() {
  local item="$1"
  local environment account_id ecr_repository_name default_region

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
  printf 'Tagging image with image tag: %s\n' "$image_tag"
  docker tag "$source_location" "$image_tag"
  printf 'Pushing image with tag: %s\n' "$image_tag"
  docker push "$image_tag"
}

process_environment() {
  local item="$1"

  case "$source_type" in
    file)
      upload_file_to_s3 "$item"
      ;;
    docker-image)
      push_image_to_ecr "$item"
      ;;
    *)
      printf 'Unrecognized source type %s - skipping\n' "$source_type" >&2
      ;;
  esac
}

iterate_environments() {
  printf '%s' "$CONFIG" | jq -c '{dev,prod} | to_entries | .[]'
}

upload_artifact_to_environments() {
  iterate_environments | while read -r item; do
    process_environment "$item"
  done
}

summarize_upload() {
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
  configure_aws_credentials
  package_artifact
  upload_artifact_to_environments
  summarize_upload
}

main "$@"
