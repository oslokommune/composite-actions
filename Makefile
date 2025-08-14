.PHONY: docs

# Update README.md files from action.yml
docs:
	find . -name "action.yml" -type f -exec sh -c './action_to_md.py "$$1" | ./replace_between.py --target "$$(dirname "$$1")/README.md" --section BOILERPLATE --in-place --create' _ {} \;
