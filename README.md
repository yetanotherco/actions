# Reusable GitHub Actions

This repository contains reusable GitHub Actions workflows for AI-powered PR code reviews.

## Available Workflows

| Workflow | AI Model | Description |
|----------|----------|-------------|
| `pr_review_claude.yml` | Claude (Anthropic) | Uses Claude Code Action with custom prompts |
| `pr_review_codex.yml` | Codex (OpenAI) | Uses OpenAI Codex Action |
| `pr_review_kimi.yml` | Kimi (Moonshot AI) | Sends the diff to the Moonshot API |
| `pr_review_openrouter.yml` | Any OpenRouter model (DeepSeek, Grok, Gemini, ...) | Sends the diff to the OpenRouter API |

## Review Prompt

Every workflow takes its review focus from the first of:

1. The `custom_prompt` input, if set
2. `.github/prompts/ai-review.md` in your repository, read from the PR's base branch, so a PR cannot change the prompt that reviews it
3. [`.github/prompts/ai-review.md`](.github/prompts/ai-review.md) in this repository, at the commit your workflow pins

Reading a prompt file fails the review on any error other than the file not existing.

## Usage

### Claude PR Review

```yaml
name: Claude Code Review

on:
  pull_request:
    types: [opened, ready_for_review]
  issue_comment:
    types: [created]

jobs:
  claude-review:
    if: |
      (github.event_name == 'pull_request' &&
       github.event.pull_request.head.repo.full_name == github.repository) ||
      (github.event_name == 'issue_comment' &&
       github.event.issue.pull_request &&
       contains(github.event.comment.body, '/claude') &&
       contains(fromJson('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.comment.author_association))
    uses: yetanotherco/actions/.github/workflows/pr_review_claude.yml@main
    secrets:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

**Inputs:**

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `custom_prompt` | No | `''` | Review focus; overrides `.github/prompts/ai-review.md` |
| `model` | No | `claude-opus-5-5` | Claude model to use |
| `max_turns` | No | `30` | Max turns for Claude |

**Secrets:**

| Secret | Required | Description |
|--------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key |

---

### Codex PR Review

```yaml
name: Codex Code Review

on:
  pull_request:
    types: [opened, ready_for_review]
  issue_comment:
    types: [created]

jobs:
  codex-review:
    if: |
      (github.event_name == 'pull_request' &&
       github.event.pull_request.head.repo.full_name == github.repository) ||
      (github.event_name == 'issue_comment' &&
       github.event.issue.pull_request &&
       contains(github.event.comment.body, '/codex') &&
       contains(fromJson('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.comment.author_association))
    uses: yetanotherco/actions/.github/workflows/pr_review_codex.yml@main
    secrets:
      OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

**Inputs:**

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `custom_prompt` | No | `''` | Review focus; overrides `.github/prompts/ai-review.md` |
| `model` | No | `gpt-6.1-sol` | Codex model to use |

**Secrets:**

| Secret | Required | Description |
|--------|----------|-------------|
| `OPENAI_API_KEY` | Yes | OpenAI API key |

---

### Kimi PR Review

Sends the PR diff to the Moonshot API and posts the answer as a PR comment.

```yaml
name: Kimi Code Review

on:
  pull_request:
    types: [opened, ready_for_review]
  issue_comment:
    types: [created]

jobs:
  kimi-review:
    if: |
      (github.event_name == 'pull_request' &&
       github.event.pull_request.head.repo.full_name == github.repository) ||
      (github.event_name == 'issue_comment' &&
       github.event.issue.pull_request &&
       contains(github.event.comment.body, '/kimi') &&
       contains(fromJson('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.comment.author_association))
    uses: yetanotherco/actions/.github/workflows/pr_review_kimi.yml@main
    secrets:
      KIMI_API_KEY: ${{ secrets.KIMI_API_KEY }}
```

**Inputs:**

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `custom_prompt` | No | `''` | Review focus; overrides `.github/prompts/ai-review.md` |
| `model` | No | `kimi-k3.1-pro` | Model to use |
| `reasoning_effort` | No | `high` | Reasoning effort: `low`, `high` or `max` |
| `max_lines` | No | `10000` | Max lines of diff to review (truncates if larger) |
| `max_bytes` | No | `2400000` | Max bytes of diff to review, applied after `max_lines` |

**Secrets:**

| Secret | Required | Description |
|--------|----------|-------------|
| `KIMI_API_KEY` | Yes | Moonshot API key |

---

### OpenRouter PR Reviews (DeepSeek, Grok, Gemini)

Sends the PR diff to any [OpenRouter](https://openrouter.ai/models) model and posts the answer as a PR comment. One `OPENROUTER_API_KEY` covers every model: add one job per model, with the values from the table below. The example uses DeepSeek.

```yaml
name: DeepSeek Code Review

on:
  pull_request:
    types: [opened, ready_for_review]
  issue_comment:
    types: [created]

jobs:
  deepseek-review:
    if: |
      (github.event_name == 'pull_request' &&
       github.event.pull_request.head.repo.full_name == github.repository) ||
      (github.event_name == 'issue_comment' &&
       github.event.issue.pull_request &&
       contains(github.event.comment.body, '/deepseek') &&
       contains(fromJson('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.comment.author_association))
    uses: yetanotherco/actions/.github/workflows/pr_review_openrouter.yml@main
    with:
      model: deepseek/deepseek-v4-pro-0813
      reasoning_effort: high
      max_bytes: 2400000
    secrets:
      OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
```

| Model | Command | `reasoning_effort` | `max_bytes` |
|-------|---------|--------------------|-------------|
| `deepseek/deepseek-v4-pro-0813` | `/deepseek` | `high` (`max`, `high`, `low`, `none`) | `2400000` (1M context) |
| `x-ai/grok-4.7` | `/grok` | `medium` (`low`, `medium`, `high`, `xhigh`) | `1200000` (500K context) |
| `google/gemini-4-argon` | `/gemini` | `medium` (`low`, `medium`, `high`) | `2400000` (1M context) |

**Inputs:**

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `custom_prompt` | No | `''` | Review focus; overrides `.github/prompts/ai-review.md` |
| `model` | Yes | - | OpenRouter model ID (see table) |
| `reasoning_effort` | No | `medium` | Reasoning effort. OpenRouter maps it to the nearest level the model supports |
| `max_lines` | No | `10000` | Max lines of diff to review (truncates if larger) |
| `max_bytes` | No | `1200000` | Max bytes of diff to review, applied after `max_lines`. Raise it for 1M-context models |

**Secrets:**

| Secret | Required | Description |
|--------|----------|-------------|
| `OPENROUTER_API_KEY` | Yes | OpenRouter API key |

Requests set `provider.data_collection: deny`, so OpenRouter only routes the diff to providers that don't store or train on it, and `provider.require_parameters: true`, so it skips providers that would ignore the reasoning settings. The job log shows which provider served each review.

### Kimi and OpenRouter notes

The diff comes from git, so PRs over the pulls API's 20000-line diff limit still get reviewed. A truncated diff is flagged in the review comment. A failed API call or a response that stops early posts an error comment and fails the job.

---

## Setup

1. Add the required API key as a secret in your repository settings
2. Create a workflow file in `.github/workflows/` using one of the examples above
3. Put your review focus in `.github/prompts/ai-review.md` (see [Review Prompt](#review-prompt))

## Versioning

The examples use `@main` for brevity. Pin callers to the commit SHA of a [release tag](https://github.com/yetanotherco/actions/tags) instead:

```yaml
uses: yetanotherco/actions/.github/workflows/pr_review_claude.yml@<tag commit SHA> # <tag>
```

## Triggering Reviews

All workflows support two trigger methods:

1. **Automatic on PR** - Runs when a PR is opened or marked ready for review
2. **Manual via comment** - Comment on a PR to trigger a review:
   - `/claude` - Claude PR Review (custom prompt)
   - `/codex` - Codex PR Review
   - `/kimi` - Kimi PR Review
   - `/deepseek`, `/grok`, `/gemini` - OpenRouter PR Review, with the commands from the OpenRouter table

Only repository owners, members, and collaborators can trigger reviews via comments.

## Security

### Fork PR Protection

All workflows include built-in protection against fork PR secret exposure. When triggered via `issue_comment` on a fork PR, the workflow will block execution with an error to prevent secrets from being exposed to untrusted code.

**Recommended:** Add fork protection in your consumer workflow's `if` condition for `pull_request` events:

```yaml
if: |
  (github.event_name == 'pull_request' &&
   github.event.pull_request.head.repo.full_name == github.repository) ||
  ...
```

This provides defense-in-depth: consumer repos explicitly skip fork PRs, and the shared workflow enforces it as a fail-safe.
