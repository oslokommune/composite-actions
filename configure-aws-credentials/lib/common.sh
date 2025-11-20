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

require_cmd() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 || die "Missing required command: $cmd"
}

require_cmd jq
require_cmd aws

authenticate_via_oidc() {
  local role_arn="$1"
  local aws_region="${2:-eu-north-1}"
  local session_name
  session_name="GitHubAction-$(date +%s)"

  : "${ACTIONS_ID_TOKEN_REQUEST_URL:?Missing ACTIONS_ID_TOKEN_REQUEST_URL}"
  : "${ACTIONS_ID_TOKEN_REQUEST_TOKEN:?Missing ACTIONS_ID_TOKEN_REQUEST_TOKEN}"

  require_cmd curl

  log_info "Authenticating to $role_arn via OIDC..."

  local oidc_response oidc_token
  if ! oidc_response="$(curl -sSLS "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=sts.amazonaws.com" \
    -H "User-Agent: actions/oidc-client" \
    -H "Authorization: Bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN")"; then
    die "Failed to reach GitHub OIDC endpoint"
  fi

  oidc_token="$(printf '%s' "$oidc_response" | jq -r '.value')"
  if [[ -z "$oidc_token" || "$oidc_token" == "null" ]]; then
    die "Failed to obtain OIDC token from GitHub. Is 'permissions: id-token: write' set?"
  fi

  local credentials_json
  if ! credentials_json="$(aws sts assume-role-with-web-identity \
    --role-arn "$role_arn" \
    --role-session-name "$session_name" \
    --web-identity-token "$oidc_token" \
    --duration-seconds 900 \
    --region "$aws_region" \
    --output json)"; then
    die "Failed to assume role $role_arn with web identity"
  fi

  AWS_ACCESS_KEY_ID="$(printf '%s' "$credentials_json" | jq -r '.Credentials.AccessKeyId')"
  AWS_SECRET_ACCESS_KEY="$(printf '%s' "$credentials_json" | jq -r '.Credentials.SecretAccessKey')"
  AWS_SESSION_TOKEN="$(printf '%s' "$credentials_json" | jq -r '.Credentials.SessionToken')"

  if [[ -z "$AWS_ACCESS_KEY_ID" || "$AWS_ACCESS_KEY_ID" == "null" ]]; then
    die "Failed to parse AWS credentials from STS response"
  fi

  export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
}

clear_credentials() {
  unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_PROFILE AWS_REGION AWS_DEFAULT_REGION
}

environment_defined() {
  : "${CONFIG:?Missing CONFIG}"
  local environment="$1"
  printf '%s' "$CONFIG" | jq -e --arg env "$environment" '.[$env] != null' >/dev/null 2>&1
}

environment_value() {
  : "${CONFIG:?Missing CONFIG}"
  local environment="$1" key="$2"
  printf '%s' "$CONFIG" | jq -e -r --arg env "$environment" --arg k "$key" '.[$env][$k]'
}
