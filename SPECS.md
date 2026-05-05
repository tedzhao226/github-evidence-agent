# CloudBees Agentic Software Engineer Assessment

## Product

Build a small CLI agent that answers questions about a public GitHub repository.
The CLI should support both one-shot questions and a long-running chat loop where the user can ask follow-up questions in the same repository context.

Default sample run:

```bash
make run PROMPT="For pydantic/pydantic-ai, how does this repository support observability or tracing, and where is that implemented?"
```

Interactive run:

```bash
make chat
```

The sample repository is `pydantic/pydantic-ai` because it connects naturally to the chosen framework and observability stack.

## Tech

- Python 3.11+
- `uv` for dependency management.
- `Makefile` for setup, run, chat, sample, and eval commands.
- Pydantic AI as the agent framework.
- OpenAI as the default model provider via `OPENAI_API_KEY`.
- Hosted Pydantic Logfire for observability via `LOGFIRE_TOKEN`.
- Quiet chat output via `LOGFIRE_CONSOLE=false`.
- Optional `GITHUB_TOKEN` to reduce GitHub API rate-limit risk.
- YAML prompt layers through `PROMPT_CONFIG`, defaulting to `prompts/agent.yaml`.

The agent exposes four tools:

- README lookup through the GitHub API.
- Issues lookup through the GitHub API.
- Recent commits lookup through the GitHub API.
- Bounded code search through a shallow temporary clone.

Pydantic models should define evidence results, final answer, eval case shape, and local trace JSON.

The local trace JSON should record session id, tool calls, evidence summary, fallback inference, final answer, full evidence list, and conversation turn.
Logfire spans should wrap each agent turn and each tool call.

## CLI Behavior

`cloudbees-agent` starts a chat loop.
The user can keep asking questions until `/exit` or `/quit`.
The first user message must mention a repository as `owner/name` or a GitHub URL.
If a later message mentions a different repository, the CLI switches to that repository and starts a fresh conversation.
Each CLI run should have a session id.
Code-search clones should be stored under `CLONE_ROOT/<session-id>/<owner-repo>`.

The chat loop supports:

- `/help`: print commands.
- `/clear`: clear in-memory conversation history.
- `/exit`: leave the loop.
- `/quit`: leave the loop.

`cloudbees-agent "For owner/name, ..."` runs one turn and exits.
This mode is used by `make run` and `make sample`.

## Eval Design

Evaluate agent behavior, not just the final answer.

Use a small fixture-backed eval suite with 8-12 cases across README, issues, commits, code search, and fallback behavior.
Each eval case should define:

- Repository and question.
- Mocked tool outputs.
- Expected first tool.
- Expected fallback, if any.
- Required evidence fields.

Score the agent on:

- First tool correctness.
- Fallback correctness.
- Final answer grounding against returned evidence.
- Trace completeness.

Keep live GitHub calls out of deterministic evals.
Use manual smoke tests for live OpenAI, GitHub, and Logfire checks.

The eval command should generate a concise summary with first tool accuracy, fallback success rate, grounded answer rate, and trace completeness.

## Failure Mode

Demonstrate bad or empty evidence.
If the first tool returns no useful evidence and a later tool returns useful evidence, the agent should record the fallback path instead of inventing an answer.

Example: issue search returns no relevant issues for an observability question, so the agent calls README or code search and records that path in both local trace JSON and Logfire.

## Deliverable

- Agent implementation.
- Conversational CLI.
- `pyproject.toml` for `uv` dependencies.
- `Makefile` with setup, run, chat, sample, and eval commands.
- `README.md` or `SPECS.md` with product, tech, deliverable, reference, and eval notes.
- `sample_run.txt` showing one complete successful live run.
- Eval fixtures plus eval summary output.
- `REFLECTION.md` with the required 200-400 word coding assistant reflection.

## Test Plan

- Unit test GitHub URL parsing and repo detection from conversation text.
- Unit test prompt YAML loading.
- Unit test interactive CLI command handling.
- Unit test multi-turn session history.
- Unit test empty evidence fallback inference.
- Unit test trace JSON shape and session id.
- Run the fixture-backed eval suite.
- Manually run smoke tests and inspect terminal output, `sample_run.txt`, local trace JSON, and Logfire trace.

## Assumptions

- Public GitHub repositories are in scope.
- Private repositories are out of scope.
- One active repository is used at a time.
- Conversation history is in memory only.
- The clone is temporary, read-only, and scoped under a session id.
- No GitHub write operations are allowed.
- Code search is capped and should ignore vendor/build directories.

## Reference

- [Assignment prompt](inbox/CloudBees%20-%20Conversation%20Starter%20-%20Agentic%20Software%20Engineer.md)
- [Original PDF](inbox/CloudBees%20-%20Conversation%20Starter%20-%20Agentic%20Software%20Engineer.pdf)
- [Pydantic AI Logfire docs](https://pydantic.dev/docs/ai/integrations/logfire/)
- [Pydantic Logfire getting started](https://pydantic.dev/docs/logfire/get-started/)
