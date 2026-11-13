# Reusable GitHub Actions

This repository contains reusable GitHub Actions workflows for AI-powered PR code reviews.

## Available Workflows

| Workflow | AI Model | Description |
|----------|----------|-------------|
| `pr_review_claude.yml` | Claude (Anthropic) | Uses Claude Code Action with custom prompts |
| `pr_review_codex.yml` | Codex (OpenAI) | Uses OpenAI Codex Action |
| `pr_review_kimi.yml` | Kimi (Moonshot AI) | Sends the diff to the Moonshot API |
| `pr_review_openrouter.yml` | Any OpenRouter model (DeepSeek, Grok, Gemini, ...) | Sends the diff to the OpenRouter API |
| `pr_review_multi.yml` | Opus, GPT, Kimi, GLM, DeepSeek + a judge | Agentic multi-model security review; one comment with findings checked against the code ([details](#multi-model-pr-review)) |

## Review Prompt

Every single-model workflow takes its review focus from the first of (the multi-model review has its own prompts and a per-repository [config](#repository-config)):

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

### Multi-model PR Review

`pr_review_multi.yml` runs a security review with several models and posts **one** comment.

1. **Reviewers.** Opus 5.5 (effort xhigh), GPT 6.1 Sol, Kimi K3.1, GLM 5.3 and DeepSeek V4 Pro review the PR in parallel. Each is an agent (Claude Code or Codex) in a read-only sandbox with no network, so it reads callers, checks and types outside the diff. A soft budget ends each reviewer at about 50 commands or 30 minutes.
2. **Housekeeping.** A cheap model (DeepSeek V4 Flash) lists stale docs, missing tests, leftovers and inconsistencies, separately from the security review.
3. **Merge and judge.** Duplicate findings are merged. A judge (GPT 6.1 Sol) checks every finding against the code, sees every reviewer's claim, and marks each one a bug, suspected, pre-existing, or likely not a problem.
4. **Comment.** It opens with a table of findings to check (severity, status, which models found it, where). Below that come collapsed sections: details, duplicates, pre-existing bugs, findings the judge dismissed (with its reason), and housekeeping. A last line gives what the review cost.

```yaml
name: AI Review

on:
  issue_comment:
    types: [created]

permissions: {}

jobs:
  review:
    if: |
      github.event.issue.pull_request &&
      startsWith(github.event.comment.body, '/review') &&
      contains(fromJson('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.comment.author_association)
    uses: yetanotherco/actions/.github/workflows/pr_review_multi.yml@<tag commit SHA> # <tag>
    permissions:
      contents: read
      pull-requests: write
      actions: read
    with:
      command: /review
    secrets:
      OPENROUTER_API_KEY: ${{ secrets.AI_REVIEW_OPENROUTER_KEY }}
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

The workflow also accepts `pull_request` events, for example a `labeled` trigger. Block fork PRs in the caller's `if`, as in the examples above. The workflow refuses them too.

**Commands.** Options go after the command, on the first line of the comment:

| Comment | Effect |
|---|---|
| `/review` | Review the PR |
| `/review budget=80` | Use $80 for this review (at most `max_budget`) |
| `/review continue` | Review only the parts that an earlier, budget-limited review of the same commit skipped; finished parts are reused |

**Inputs:**

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `command` | No | `/review` | The caller's comment command, used in the hint a partial review prints |
| `pr_budget` | No | `0` | USD limit per review. `0` = $20 for a PR reviewed in one part, $50 for a PR split into parts |
| `max_budget` | No | `100` | Highest budget a `budget=N` comment may ask for |
| `lanes` | No | `''` | Reviewer models, comma-separated: `claude`, `gpt`, `kimi`, `glm`, `deepseek`, `gemini`, `grok`, `fable`. Empty = the first five |
| `config_dir` | No | `.github/ai-review` | Directory with `config.toml` and `threat_model.md` in your repository |
| `housekeeping` | No | `true` | Run the housekeeping reviewer |

**Secrets:**

| Secret | Required | Description |
|--------|----------|-------------|
| `OPENROUTER_API_KEY` | Yes | OpenRouter key for every model but Claude. It must belong to the account that holds the `air-*` provider presets (see below). Give it a credit limit: that limit is the hard ceiling on spend |
| `ANTHROPIC_API_KEY` | Unless `lanes` leaves out `claude` | Anthropic key for the Opus reviewer. Use a workspace with a monthly spend limit |

The calling job must grant `contents: read`, `pull-requests: write` and `actions: read`. Only the final job, which posts the comment, gets `pull-requests: write`; the jobs that run models get read permissions only. `actions: read` lets `continue` download the earlier review.

#### Repository config

Put a `config.toml` and a `threat_model.md` in `.github/ai-review/` in your repository. Both are read from the PR's base branch, so a PR cannot change how it is reviewed. Without them every file counts as security-sensitive, which is safe but costs more on large PRs.

```toml
threat_model = "threat_model.md"   # what matters in this repository; goes into every reviewer prompt

# Files that get every reviewer. Others (docs, tests, tooling) get `nonsensitive_lanes` only.
sensitive = ["crates/core/**", "contracts/src/**", "Cargo.toml"]
nonsensitive_lanes = ["claude", "gpt", "kimi"]

# Not reviewed at all (lockfiles, binaries and generated or vendored code are skipped already).
ignore = ["benches/**", "**/*.json"]

# Extra test file patterns, on top of tests/, *_test.rs, *.test.ts, ...
test_globs = ["fixtures/**"]

# Review order for large PRs. Parts matching these come first and, in a split PR, get twice the
# per-reviewer budget.
priority = ["contracts/src/**", "crates/core/**"]

# Large PRs are split into parts by Cargo package, else by the first `unit_depth` path components.
unit_depth = 2
```

`threat_model.md` lists what a reviewer should care about most: core guarantees, untrusted inputs, known-dangerous areas. See [`.github/ai-review/`](.github/ai-review/) in this repository for an example.

#### Cost and large PRs

- **Typical cost:** about $3 for a PR reviewed in one part, at API prices. Opus is about 60% of it. The comment's last line shows the cost of each review. In CI this figure is computed from token counts and published prices. The OpenRouter bill is exact, but a key shared by several repositories can't attribute it to one review.
- **Budget:** a PR over about 4,000 changed lines is split into parts of about 2,500 lines. The parts are reviewed in order (`priority`, then `sensitive`, then the rest) until the PR budget runs out, keeping enough for the judge (at least 15% of a split PR's budget). Parts left unreviewed are listed at the top of the comment; `/review continue` reviews them with a new budget.
- **Hard limits:** each reviewer stops at its soft budget, and it is killed at its lane cap ($3–4, estimated). A watchdog stops the whole review at the PR budget. The ceiling OpenRouter enforces is the key's credit limit.
- **Time:** a one-part PR takes 15–35 minutes. The parts of a split PR run one after another, so a PR with many parts can approach the job's 330-minute limit.

#### Provider presets

Each OpenRouter model is called through a preset (`model@preset/air-...`, listed in [`ai-review/presets.json`](ai-review/presets.json)). The preset pins the provider, for example Anthropic's and OpenAI's own servers, or full-precision hosts for open models. Presets belong to an OpenRouter account, so `OPENROUTER_API_KEY` must come from that account. Its privacy setting (no providers that train on prompts) also applies to every key in it.

#### Running it locally

`ai-review/run_pilot.py` is the runner the workflow calls; `python3 ai-review/run_pilot.py --help` lists its options. Locally it reads keys from the macOS Keychain; in CI, from the environment. `--dry-run` shows the parts, the reviewers and the estimated cost without making a paid call.

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
   - In this repository, `/review` runs the [multi-model review](#multi-model-pr-review); callers choose their own command

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
