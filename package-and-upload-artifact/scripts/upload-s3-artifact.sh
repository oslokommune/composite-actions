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
package_tmp_dir=""

cleanup() {
  if [[ -n "${aws_config_file:-}" ]]; then
    rm -f "$aws_config_file"
  fi
  if [[ -n "$package_tmp_dir" && -d "$package_tmp_dir" ]]; then
    rm -rf "$package_tmp_dir"
  fi
}
trap cleanup EXIT

configure_aws

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
  if [[ -n "$extension" ]]; then
    tag="$tag.$extension"
  fi
}

upload_file_artifact() {
  local environment="$1"
  local bucket_name

  bucket_name="$(environment_value "$environment" artifactBucketName)"

  log_info "Uploading $source_location to S3 as $tag in $environment"

  export AWS_PROFILE="$environment"
  aws s3 cp "$source_location" "s3://$bucket_name/$tag"
}

case "$source_type" in
folder)
  log_info "Compressing folder artifact into archive for upload"
  archive_folder_source
  ;;
file) ;;
docker-image)
  die "upload-s3-artifact.sh only supports file or folder sources"
  ;;
*)
  die "Unsupported source type: $source_type"
  ;;
esac

log_info "Preparing file artifact for upload to S3"
append_file_extension_suffix

for environment in dev prod; do
  if environment_defined "$environment"; then
    upload_file_artifact "$environment"
  fi
done

write_github_summary "$tag"
