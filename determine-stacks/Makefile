.PHONY: run test

# Run the script with example input
run: run-dispatch

run-change:
	@echo "Running determine_stacks.py with example input..."
	@export CHANGED_FILES='testdata/stacks/dev/networking/main.tf,testdata/stacks/dev/app-too-tikki/main.tf,testdata/stacks/dev/databases/main.tf,testdata/stacks/prod/remote-state/main.tf,testdata/stacks/prod/iam/roles.tf' && \
	uv run determine_stacks.py

run-dispatch:
	@export SELECTED_STACKS="testdata/stacks/dev/*" && \
	uv run determine_stacks.py

# Run unit tests
test:
	@echo "Running tests with pytest..."
	uv run pytest test_determine_stacks.py -vv
