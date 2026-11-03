# Reusable GitHub Actions

This repository contains reusable GitHub Actions workflows for AI-powered PR code reviews.

## Available Workflows

| Workflow | AI Model | Description |
|----------|----------|-------------|
| `pr_review_claude.yml` | Claude (Anthropic) | Uses Claude Code Action with custom prompts |
| `pr_review_codex.yml` | Codex (OpenAI) | Uses OpenAI Codex Action |
| `pr_review_kimi.yml` | Kimi (Moonshot AI) | Sends the diff to the Moonshot API |
| `pr_review_deepseek.yml` | DeepSeek | Sends the diff to the DeepSeek API |
| `pr_review_grok.yml` | Grok (xAI) | Sends the diff to the xAI Responses API |
| `pr_review_gemini.yml` | Gemini (Google) | Sends the diff to the Gemini API |

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
    with:
      custom_prompt: |
        1. **Security vulnerabilities** - Label by criticality (Critical/High/Medium/Low)
           - Solidity: e.g. reentrancy, access control, integer issues
           - Rust: e.g. unsafe blocks, error handling, panics
           - Web/API: e.g. SQL injection, auth bypass, input validation

        2. **Potential bugs** - Logic errors, edge cases, race conditions

        3. **Performance issues** - Only significant (O(n²), N+1 queries, etc.)

        4. **Simplicity** - Prefer simple, readable code

        Guidelines:
        - Be concise and actionable
        - Focus on real issues, not hypothetical improvements
    secrets:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

**Inputs:**

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `custom_prompt` | Yes | - | Custom review instructions (what to focus on) |
| `model` | No | `sonnet` | Claude model to use |
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
    with:
      custom_prompt: |
        1. **Security vulnerabilities** - Label by criticality (Critical/High/Medium/Low)

        2. **Potential bugs** - Logic errors, edge cases, race conditions

        3. **Performance issues** - Only significant issues

        4. **Simplicity** - Prefer simple, readable code

        Guidelines:
        - Be concise and actionable
        - Focus on real issues, not hypothetical improvements
    secrets:
      OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

**Inputs:**

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `custom_prompt` | Yes | - | Custom review instructions (what to focus on) |

**Secrets:**

| Secret | Required | Description |
|--------|----------|-------------|
| `OPENAI_API_KEY` | Yes | OpenAI API key |

---

### Kimi, DeepSeek, Grok and Gemini PR Reviews

These workflows send the PR diff to the provider's API and post the answer as a PR comment. They share one interface; the example uses Kimi.

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
    with:
      custom_prompt: |
        1. **Security vulnerabilities** - Label by criticality (Critical/High/Medium/Low)

        2. **Potential bugs** - Logic errors, edge cases, race conditions

        3. **Performance issues** - Only significant issues

        4. **Simplicity** - Prefer simple, readable code

        Guidelines:
        - Be concise and actionable
        - Focus on real issues, not hypothetical improvements
    secrets:
      KIMI_API_KEY: ${{ secrets.KIMI_API_KEY }}
```

| Workflow | Secret | Command | `model` | `reasoning_effort` | `max_bytes` |
|----------|--------|---------|---------|--------------------|-------------|
| `pr_review_kimi.yml` | `KIMI_API_KEY` | `/kimi` | `kimi-k3.1-pro` | `high` (`low`, `high`, `max`) | `2400000` |
| `pr_review_deepseek.yml` | `DEEPSEEK_API_KEY` | `/deepseek` | `deepseek-v4-pro` | `high` (`none`, `low`, `high`, `max`) | `2400000` |
| `pr_review_grok.yml` | `XAI_API_KEY` | `/grok` | `grok-4.7` | `medium` (`low`, `medium`, `high`, `xhigh`) | `1200000` |
| `pr_review_gemini.yml` | `GEMINI_API_KEY` | `/gemini` | `gemini-4-argon` | `medium` (`low`, `medium`, `high`) | `2400000` |

**Inputs:**

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `custom_prompt` | Yes | - | Custom review instructions (what to focus on) |
| `model` | No | See table | Model to use |
| `reasoning_effort` | No | See table | Reasoning effort (Gemini: thinking level) |
| `max_lines` | No | `10000` | Max lines of diff to review (truncates if larger) |
| `max_bytes` | No | See table | Max bytes of diff to review, applied after `max_lines` |

The diff comes from git, so PRs over the pulls API's 20000-line diff limit still get reviewed. A truncated diff is flagged in the review comment. A failed API call or a response that stops early posts an error comment and fails the job.

---

## Setup

1. Add the required API key as a secret in your repository settings
2. Create a workflow file in `.github/workflows/` using one of the examples above
3. Customize the `custom_prompt` to match your project's review criteria

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
   - `/deepseek` - DeepSeek PR Review
   - `/grok` - Grok PR Review
   - `/gemini` - Gemini PR Review

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
