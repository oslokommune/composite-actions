.PHONY: aider a

aider:
	export AWS_REGION="eu-central-1" && \
	aider --model 'anthropic.claude-3-5-sonnet-20240620-v1:0' --no-show-model-warnings

a: aider
