# Reusable GitHub Actions

This repository contains reusable GitHub Actions workflows for AI-powered PR code reviews.

## Available Workflows

| Workflow | AI Model | Description |
|----------|----------|-------------|
| `pr_review_claude.yml` | Claude (Anthropic) | Uses Claude Code Action with inline commenting |
| `pr_review_codex.yml` | Codex (OpenAI) | Uses OpenAI Codex Action |
| `pr_review_kimi.yml` | Kimi (Moonshot AI) | Uses Moonshot API directly |

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
      (github.event_name == 'pull_request') ||
      (github.event_name == 'issue_comment' &&
       github.event.issue.pull_request &&
       contains(github.event.comment.body, '/claude') &&
       contains(fromJson('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.comment.author_association))
    uses: yetanothercompany/actions/.github/workflows/pr_review_claude.yml@main
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
      (github.event_name == 'pull_request') ||
      (github.event_name == 'issue_comment' &&
       github.event.issue.pull_request &&
       contains(github.event.comment.body, '/codex') &&
       contains(fromJson('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.comment.author_association))
    uses: yetanothercompany/actions/.github/workflows/pr_review_codex.yml@main
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

### Kimi PR Review

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
    uses: yetanothercompany/actions/.github/workflows/pr_review_kimi.yml@main
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

**Inputs:**

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `custom_prompt` | Yes | - | Custom review instructions (what to focus on) |
| `max_lines` | No | `10000` | Max lines of diff to review (truncates if larger) |

**Secrets:**

| Secret | Required | Description |
|--------|----------|-------------|
| `KIMI_API_KEY` | Yes | Moonshot AI API key |

---

## Setup

1. Add the required API key as a secret in your repository settings
2. Create a workflow file in `.github/workflows/` using one of the examples above
3. Customize the `custom_prompt` to match your project's review criteria

## Triggering Reviews

All workflows support two trigger methods:

1. **Automatic on PR** - Runs when a PR is opened or marked ready for review
2. **Manual via comment** - Comment `/claude`, `/codex`, or `/kimi` on a PR to trigger a review

Only repository owners, members, and collaborators can trigger reviews via comments.
