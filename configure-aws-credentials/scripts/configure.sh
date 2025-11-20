#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

: "${CONFIG:?Missing CONFIG}"
: "${ENVIRONMENT:?Missing ENVIRONMENT}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
source "$repo_root/lib/common.sh"

environment="$ENVIRONMENT"

if ! environment_defined "$environment"; then
  die "Environment '$environment' not defined in config"
fi

role_arn="$(environment_value "$environment" artifactRoleArn)"
default_region="$(environment_value "$environment" defaultRegion)" || die "Missing defaultRegion for $environment"

authenticate_via_oidc "$role_arn" "$default_region"
export AWS_REGION="$default_region"
export AWS_DEFAULT_REGION="$default_region"

log_info "AWS credentials configured for environment: $environment"
