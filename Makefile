PYTHON := PYTHONPATH=src uv run python
PYTEST := PYTHONPATH=src uv run pytest
PROMPT ?= For pydantic/pydantic-ai, how does this repository support observability or tracing, and where is that implemented?

.PHONY: help setup run chat sample eval test smoke test-all

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "%-12s %s\n", $$1, $$2}'

setup: ## Install dependencies
	uv sync

run: ## Run the agent once
	$(PYTHON) -m cloudbees_agent.cli "$(PROMPT)"

chat: ## Start the interactive repo chat
	$(PYTHON) -m cloudbees_agent.cli

sample: ## Run both CLI sample modes and write sample_run.txt
	@printf '# Single Conversation\n\n```bash\nmake run PROMPT="%s"\n```\n\n```text\n' "$(PROMPT)" > sample_run.txt
	@$(PYTHON) -m cloudbees_agent.cli "$(PROMPT)" >> sample_run.txt
	@printf '```\n\n# Long Conversation\n\n```bash\nmake chat\n```\n\n```text\n' >> sample_run.txt
	@printf 'For pydantic/pydantic-ai, how does this repository support observability or tracing?\nWhere is the most relevant implementation file?\n/exit\n' | $(PYTHON) -m cloudbees_agent.cli >> sample_run.txt
	@printf '```\n' >> sample_run.txt

eval: ## Run deterministic fixture-backed evals
	$(PYTHON) -m cloudbees_agent.eval

test: ## Run tests
	$(PYTEST) -m "not smoke" tests

smoke: ## Manually run real API smoke tests
	$(PYTEST) -m smoke tests/smoke_test.py

test-all: ## Run offline tests, then manual smoke tests
	$(PYTEST) -m "not smoke" tests
	$(PYTEST) -m smoke tests/smoke_test.py
