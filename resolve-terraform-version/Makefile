.PHONY: run test test-against-real-terraform

# Resolve the version for a stack, e.g. `make run STACK_DIR=../some-iac/stacks/dev/dns`
run:
	@STACK_DIR="$(STACK_DIR)" uv run resolve_terraform_version.py

test:
	uv run pytest -v

# Also check the constraint matcher against a real Terraform binary. Uses whichever
# terraform is on PATH; override with TERRAFORM_BINARIES to check several at once,
# e.g. TERRAFORM_BINARIES=/path/to/1.10.5/terraform:/path/to/1.2.9/terraform
test-against-real-terraform: TERRAFORM_BINARIES ?= $(shell command -v terraform)
test-against-real-terraform:
	@test -n "$(TERRAFORM_BINARIES)" || { echo "No terraform on PATH; set TERRAFORM_BINARIES"; exit 1; }
	TERRAFORM_BINARIES="$(TERRAFORM_BINARIES)" uv run pytest -v
