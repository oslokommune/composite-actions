#!/usr/bin/env bash
# Common helpers shared between artifact scripts.

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

configure_aws() {
  : "${AWSCREDS:?Missing AWSCREDS}"
  aws_config_file="$(mktemp)"
  printf '%s\n' "$AWSCREDS" >"$aws_config_file"
  export AWS_CONFIG_FILE="$aws_config_file"
}

environment_defined() {
  : "${CONFIG:?Missing CONFIG}"
  local environment="$1"
  printf '%s' "$CONFIG" | jq -e ".${environment} != null" >/dev/null 2>&1
}

environment_value() {
  : "${CONFIG:?Missing CONFIG}"
  local environment="$1" key="$2"
  printf '%s' "$CONFIG" | jq -e -r ".${environment}.${key}"
}

write_github_summary() {
  local final_tag="$1"
  : "${PARTIAL_WORKFLOW_DISPATCH_URL:?Missing PARTIAL_WORKFLOW_DISPATCH_URL}"
  : "${GITHUB_WORKFLOW_REF:?Missing GITHUB_WORKFLOW_REF}"
  : "${GITHUB_OUTPUT:?Missing GITHUB_OUTPUT}"
  : "${GITHUB_STEP_SUMMARY:?Missing GITHUB_STEP_SUMMARY}"
  : "${GITHUB_ENV:?Missing GITHUB_ENV}"

  local workflow_filename workflow_dispatch_url
  workflow_filename="$(basename "${GITHUB_WORKFLOW_REF%%@*}")"
  workflow_dispatch_url="$PARTIAL_WORKFLOW_DISPATCH_URL/$workflow_filename"

  printf 'tag=%s\n' "$final_tag" >>"$GITHUB_OUTPUT"
  printf 'ARTIFACT_TAG=%s\n' "$final_tag" >>"$GITHUB_ENV"
  cat <<EOF >>"$GITHUB_STEP_SUMMARY"
Built and uploaded artifact with tag:
\`\`\`
$final_tag
\`\`\`

---

_To manually deploy the artifact, copy the tag and pass it in through a [workflow dispatch]($workflow_dispatch_url)_
EOF
}
