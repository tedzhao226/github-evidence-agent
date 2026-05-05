# CloudBees GitHub Evidence Agent

This is a small CLI agent for the CloudBees Agentic Software Engineer assessment.
Given a public GitHub repository, it supports a long-running chat session, calls read-only GitHub evidence tools, and answers from the evidence it found.

I used Pydantic AI because the assignment values typed agent behavior and tool calls.
The live path uses OpenAI through Pydantic AI when `OPENAI_API_KEY` is present.
Offline tests use fixture tools and Pydantic AI test models, so default checks do not call live APIs.

## Run

```bash
make setup
make chat
```

One-shot mode is still available for samples and scripts:

```bash
make run PROMPT="For pydantic/pydantic-ai, how does this repository support observability or tracing, and where is that implemented?"
```

Environment:

- `OPENAI_API_KEY`: enables the live Pydantic AI agent.
- `OPENAI_MODEL`: optional, defaults to `openai:gpt-5-mini`.
- `LOGFIRE_TOKEN`: optional, sends spans to hosted Logfire when present.
- `LOGFIRE_CONSOLE`: optional, defaults to `false` so chat mode stays quiet.
- `GITHUB_TOKEN`: optional, reduces GitHub API rate-limit risk.
- `CLONE_ROOT`: optional base directory, defaults to `tmp/repos`.
- `PROMPT_CONFIG`: optional, defaults to `prompts/agent.yaml`.

Useful commands:

```bash
make test
make eval
make smoke
make sample
```

`make sample` writes `sample_run.txt`.
Each turn writes trace JSON under `traces/`.

`make test` is offline and deterministic.
`make smoke` must be triggered manually; it loads `.env`, calls real OpenAI and GitHub services, and flushes Logfire spans when `LOGFIRE_TOKEN` is set.
Smoke tests are intended for local checks, not default CI.

## Agent Flow

See [docs/architecture.md](docs/architecture.md) for the design walkthrough.
The agent prompt layers live in [prompts/agent.yaml](prompts/agent.yaml).

The agent has four read-only evidence tools:

- README lookup through the GitHub API.
- Issues lookup through the GitHub API.
- Recent commits lookup through the GitHub API.
- Bounded code search through a temporary shallow clone.

The CLI has two modes:

- `cloudbees-agent` starts a chat loop.
- `cloudbees-agent "For owner/name, ..."` runs one turn.

The chat loop supports `/help`, `/clear`, `/exit`, and `/quit`.
Conversation history is kept in memory for the current process.
The first question must mention a repository as `owner/name` or a GitHub URL.
If a later message mentions a different repository, the CLI switches to that repo and starts a fresh conversation.
Each CLI run has a session id.
Code-search clones are written under `CLONE_ROOT/<session-id>/<owner-repo>` so concurrent conversations do not share clone directories.

## Failure Mode

The demonstrated failure mode is weak or empty first evidence.
If the first tool returns no relevant items and a later tool finds evidence, the trace records the later tool as a fallback.
The agent should report uncertainty when tools do not return enough evidence.

## Evals

The eval suite is fixture-backed and does not call GitHub or OpenAI.
It covers README, issues, commits, code search, and fallback behavior.

Metrics:

- First tool correctness.
- Fallback correctness.
- Grounded answer rate.
- Trace completeness.

Run:

```bash
make eval
```

## Smoke Tests

Create a local `.env` from `.env.example` and fill in real values:

```bash
OPENAI_API_KEY=...
LOGFIRE_TOKEN=...
LOGFIRE_CONSOLE=false
GITHUB_TOKEN=...
OPENAI_MODEL=openai:gpt-5-mini
CLONE_ROOT=tmp/repos
PROMPT_CONFIG=prompts/agent.yaml
```

Then run:

```bash
make smoke
```

The smoke suite has two checks:

- Real Pydantic AI/OpenAI agent turn with tools.
- Real multi-turn agent run with GitHub evidence and Logfire `force_flush()`.

## Assumptions and Limits

Only public GitHub repositories are in scope.
The tool never performs GitHub writes.
Code search uses a temporary shallow clone and skips common vendor/build directories.
By default, fresh code-search clones are written under `tmp/repos/<session-id>/<owner-repo>`.
The final answer is intentionally compact and evidence-first rather than polished prose.
