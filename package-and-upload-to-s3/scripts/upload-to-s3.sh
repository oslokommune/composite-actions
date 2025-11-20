#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

: "${CONFIG:?Missing CONFIG}"
: "${SOURCE_TYPE:?Missing SOURCE_TYPE}"
: "${SOURCE_LOCATION:?Missing SOURCE_LOCATION}"
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

require_cmd zip

source_type="$SOURCE_TYPE"
source_location="$SOURCE_LOCATION"
tag="$TAG"
package_tmp_dir=""

cleanup() {
  if [[ -n "$package_tmp_dir" && -d "$package_tmp_dir" ]]; then
    rm -rf "$package_tmp_dir"
  fi
}
trap cleanup EXIT

archive_folder_source() {
  package_tmp_dir="$(mktemp -d)"
  (cd "$source_location" && zip -r "$package_tmp_dir/archive.zip" .)
  source_location="$package_tmp_dir/archive.zip"
  source_type="file"
}

append_file_extension_suffix() {
  local extension=""
  if [[ "$source_location" == *.* ]]; then
    extension="${source_location##*.}"
  fi
  if [[ -n "$extension" && "$tag" != *."$extension" ]]; then
    tag="$tag.$extension"
  fi
}

upload_file_to_s3() {
  local environment="$1"
  local bucket_name

  bucket_name="$(environment_value "$environment" artifactBucketName)"

  log_info "Uploading $source_location to S3 as $tag in $environment"

  aws s3 cp "$source_location" "s3://$bucket_name/$tag"
}

case "$source_type" in
folder)
  log_info "Compressing folder artifact into archive for upload"
  archive_folder_source
  ;;
file) ;;
*)
  die "Unsupported source type: $source_type"
  ;;
esac

log_info "Preparing file artifact for upload to S3"
append_file_extension_suffix

environments="$(printf '%s' "$CONFIG" | jq -r 'to_entries[] | select(.value.artifactRoleArn != null) | .key')"
if [[ -z "$environments" ]]; then
  die "No environments with artifactRoleArn defined in config"
fi

for environment in $environments; do
  if ! environment_defined "$environment"; then
    continue
  fi

  role_arn="$(environment_value "$environment" artifactRoleArn)"
  default_region="$(environment_value "$environment" defaultRegion)" || die "Missing defaultRegion for $environment"

  authenticate_via_oidc "$role_arn" "$default_region"
  export AWS_REGION="$default_region"
  export AWS_DEFAULT_REGION="$default_region"

  upload_file_to_s3 "$environment"
  clear_credentials
done

# Write summary and outputs
workflow_filename="$(basename "${GITHUB_WORKFLOW_REF%%@*}")"
workflow_dispatch_url="$PARTIAL_WORKFLOW_DISPATCH_URL/$workflow_filename"

printf 'ARTIFACT_TAG=%s\n' "$tag" >>"$GITHUB_ENV"
cat <<EOF >>"$GITHUB_STEP_SUMMARY"
Built and uploaded artifact to S3 with tag:
\`\`\`
$tag
\`\`\`

---

_To manually deploy the artifact, copy the tag and pass it in through a [workflow dispatch]($workflow_dispatch_url)_
EOF
