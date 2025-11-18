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

# Remove temporary AWS config and archive before exiting.
cleanup() {
  rm -f "$aws_config_file"
  if [[ -n "$package_tmp_dir" && -d "$package_tmp_dir" ]]; then
    rm -rf "$package_tmp_dir"
  fi
}
trap cleanup EXIT

# Print informational message with a consistent prefix.
log_info() {
  printf '[INFO] %s\n' "$*"
}

# Print errors to stderr with a consistent prefix.
log_error() {
  printf '[ERROR] %s\n' "$*" >&2
}

# Exit with an error message.
die() {
  log_error "$1"
  exit 1
}

# Write inline AWS credentials to a temp config file used by AWS CLI.
configure_aws_profiles() {
  printf '%s\n' "$AWSCREDS" >"$aws_config_file"
  export AWS_CONFIG_FILE="$aws_config_file"
}

# Zip a folder source into a temporary archive so it can be uploaded as a single file.
archive_folder_source() {
  package_tmp_dir="$(mktemp -d)"
  (cd "$source_location" && zip -r "$package_tmp_dir/archive.zip" .)
  source_location="$package_tmp_dir/archive.zip"
  source_type="file"
}

# Append file extension to the tag so consumers can infer artifact type.
# Use shell parameter expansion to derive the extension efficiently.
append_file_extension_suffix() {
  local extension=""
  if [[ "$source_location" == *.* ]]; then
    extension="${source_location##*.}"
  fi
  if [[ -n "$extension" ]]; then
    tag="$tag.$extension"
  fi
}

# Return success when the config contains the target environment key.
environment_defined() {
  local environment="$1"
  printf '%s' "$CONFIG" | jq -e ".${environment} != null" >/dev/null 2>&1
}

# Extract a single value from the config for the given environment.
environment_value() {
  local environment="$1" key="$2"
  printf '%s' "$CONFIG" | jq -e -r ".${environment}.${key}"
}

# Upload a prepared file artifact to the environment-specific S3 bucket.
upload_file_artifact() {
  local environment="$1"
  local bucket_name

  bucket_name="$(environment_value "$environment" artifactBucketName)"

  log_info "Uploading $source_location to S3 as $tag in $environment"

  export AWS_PROFILE="$environment"
  aws s3 cp "$source_location" "s3://$bucket_name/$tag"
}

# Push a Docker image artifact to the environment-specific ECR repository.
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

# Write the resulting tag to the GitHub Action summary and outputs.
write_github_summary() {
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

  case "$source_type" in
    folder)
      log_info "Compressing folder artifact into archive for upload"
      archive_folder_source
      ;;
    file | docker-image)
      # Explicitly list the supported types so we don't fall through to the error arm below.
      ;;
    *)
      die "Unsupported source type: $source_type"
      ;;
  esac

  if [[ "$source_type" == "file" ]]; then
    log_info "Preparing file artifact for upload to S3"
    append_file_extension_suffix
  fi

  # TODO: iterate environments dynamically from CONFIG instead of hardcoding dev/prod.
  for environment in dev prod; do
    if ! environment_defined "$environment"; then
      continue
    fi

    # Upload separately per environment, reusing the prepared artifact above.
    case "$source_type" in
      file)
        upload_file_artifact "$environment"
        ;;
      docker-image)
        upload_image_artifact "$environment"
        ;;
    esac
  done

  write_github_summary
}

main "$@"
