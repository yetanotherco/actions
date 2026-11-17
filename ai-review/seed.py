"""Deterministic seed construction for the AI review pilot (no LLM calls, stdlib only).

From a checkout at the PR head and the merge-base it builds a *plan*:
  - every changed file classified as code / test / docs / filtered, with line counts
    (whitespace-only changes ignored except in whitespace-sensitive files, renames and copies detected);
  - what was filtered and why (lockfile, generated/vendored, repo ignore glob, binary,
    whitespace-only, outside --paths), for the coverage table;
  - test files and the test functions they add / change / remove (tests are not put in the
    seed diff; the agentic lanes can read them in the checkout);
  - the effective size (code lines + 0.5 x test lines, after trimming) and the size class;
  - one review unit for the whole PR (marked low-risk when all its code matches `low_risk`); a PR over
    MAX_EFFECTIVE lines is not reviewed.
Per unit it renders the seed diff (whole-function context, deletion-only hunks compacted)
and the plain `-U3` diff used by the static-diff comparison mode.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

# One review per PR, up to MAX_EFFECTIVE lines; a bigger PR is not reviewed and the comment asks for it
# to be split (2026-11-17). On propamm #1 (one run per arm) a single 10k- or 21k-line review found the
# critical and the same real highs as four ~2.5k-line parts, at about half the cost; the split parts
# found only extra low-severity issues. Outside lambda_vm, almost no PR is over 10k (last 12 months).
MAX_EFFECTIVE = 20000
TEST_WEIGHT = 0.5
# Chars of diff in the prompt; above it the diff is ranked (larger changes first, low-risk last) and the rest
# is listed for the reviewers to read with their tools. Whole-function diffs run ~36-38 chars per effective
# line, so this holds ~15-16k lines whole. It was 250k while Codex, on fallback model metadata, compacted
# its context (dropping the diff into a summary) at ~245k tokens: GLM did at a 408k-char prompt, GPT at
# 795k. The model catalog run_pilot.py gives each Codex lane (CODEX_CATALOG_ENTRY) now gives Codex the
# real window (2026-11-17): 600k chars is about 150-250k tokens, far below the ~890k-token compaction
# point of the default lanes' 1M windows. Opus did not compact at 795k.
DIFF_CAP = 600_000
DELETION_COMPACT_MIN = 15  # deletion-only hunks with fewer removed lines stay in full

LOCKFILE_NAMES = {
    "Cargo.lock", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
    "bun.lock", "bun.lockb", "go.sum", "go.work.sum", "poetry.lock", "uv.lock", "Pipfile.lock",
    "pdm.lock", "flake.lock", "foundry.lock", "Gemfile.lock", "composer.lock", "mix.lock",
    "pubspec.lock", "Package.resolved", "gradle.lockfile", ".terraform.lock.hcl", "deno.lock",
}
DOC_EXTS = {".md", ".markdown", ".txt", ".rst", ".typ", ".adoc", ".asciidoc"}
DOC_NAMES = {"LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS", "CHANGELOG", "CODEOWNERS"}
NOT_DOCS = re.compile(r"(^|/)(requirements[\w.-]*|constraints[\w.-]*|CMakeLists)\.txt$")  # manifests, not docs
GENERATED_GLOBS = [
    "**/generated/**", "**/__generated__/**", "**/vendor/**", "**/third_party/**",
    "**/node_modules/**", "**/dist/**", "**/*.min.js", "**/*.min.css", "**/*.map",
    "**/*.pb.go", "**/*_pb2.py", "**/*_pb2_grpc.py", "**/*.g.dart", "**/*.snap",
    "**/*_generated.*", "**/*.generated.*", "**/*.svg",
]
TEST_GLOBS = [
    "**/tests/**", "**/test/**", "**/__tests__/**", "**/testdata/**", "**/test_data/**",
    "**/*_test.rs", "**/*_tests.rs", "**/tests.rs", "**/*_test.go", "**/test_*.py",
    "**/*_test.py", "**/conftest.py", "**/*.t.sol", "**/*.test.ts", "**/*.test.tsx",
    "**/*.test.js", "**/*.test.jsx", "**/*.spec.ts", "**/*.spec.js",
]

# Files where a whitespace-only change can change what they do (Python indentation, shell word
# splitting, YAML nesting, Makefile recipes): their counts and diffs keep whitespace. Every other
# file's ignore it (-w).
WS_EXTS = {".py", ".sh", ".bash", ".zsh", ".yml", ".yaml", ".mk"}
WS_NAMES = {"Makefile", "makefile", "GNUmakefile"}

ATTRS = ("*.rs diff=rust\n*.py diff=python\n*.go diff=golang\n*.sol diff=cpp\n*.ts diff=javascript\n"
         "*.tsx diff=javascript\n*.js diff=javascript\n*.md diff=markdown\n")

# --------------------------------------------------------------------------------------
# glob matching with ** (fnmatch alone lets * cross "/")


def _glob_re(glob: str) -> re.Pattern:
    i, out = 0, ""
    g = glob.lstrip("/")
    while i < len(g):
        if g.startswith("**/", i):
            out += r"(?:.*/)?"
            i += 3
        elif g.startswith("**", i):
            out += r".*"
            i += 2
        elif g[i] == "*":
            out += r"[^/]*"
            i += 1
        elif g[i] == "?":
            out += r"[^/]"
            i += 1
        else:
            out += re.escape(g[i])
            i += 1
    return re.compile(out + r"\Z")


_GLOB_CACHE: dict[str, re.Pattern] = {}


def glob_match(path: str, globs: list[str]) -> str | None:
    """First glob that matches path (also matches a directory glob like 'contracts/src')."""
    for g in globs:
        r = _GLOB_CACHE.get(g) or _GLOB_CACHE.setdefault(g, _glob_re(g))
        if r.match(path) or (not any(c in g for c in "*?[") and path.startswith(g.rstrip("/") + "/")):
            return g
    return None


def ws_sensitive(path: str) -> bool:
    p = PurePosixPath(path)
    return p.suffix.lower() in WS_EXTS or p.name in WS_NAMES


def _ws_flag(path: str) -> list[str]:
    """`-w` for a file whose whitespace does not matter, nothing for a whitespace-sensitive one."""
    return [] if ws_sensitive(path) else ["-w"]


# --------------------------------------------------------------------------------------
# git helpers


def git(wt: Path, *args: str, check: bool = True, attrs: Path | None = None, input_: str | None = None) -> str:
    cmd = ["git", "-C", str(wt)]
    if attrs:
        cmd += ["-c", f"core.attributesFile={attrs}"]
    cmd += list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, input=input_, errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])} failed: {r.stderr[-800:]}")
    return r.stdout


def _parse_numstat_z(out: str) -> dict[str, tuple[int | None, int | None, str | None]]:
    """path -> (added, deleted, old_path). Binary files have None counts."""
    res: dict[str, tuple[int | None, int | None, str | None]] = {}
    toks = out.split("\0")
    i = 0
    while i < len(toks):
        t = toks[i]
        if not t.strip():
            i += 1
            continue
        parts = t.split("\t", 2)   # a path may contain a tab
        if len(parts) < 3:
            i += 1
            continue
        a, d, p = parts[0].strip(), parts[1], parts[2]
        ai = int(a) if a.isdigit() else None
        di = int(d) if d.isdigit() else None
        if p == "":  # rename / copy: old and new follow as separate tokens
            old, new = toks[i + 1], toks[i + 2]
            res[new] = (ai, di, old)
            i += 3
        else:
            res[p] = (ai, di, None)
            i += 1
    return res


def _parse_name_status_z(out: str) -> dict[str, tuple[str, str | None]]:
    res: dict[str, tuple[str, str | None]] = {}
    toks = [t for t in out.split("\0")]
    i = 0
    while i < len(toks):
        st = toks[i]
        if not st:
            i += 1
            continue
        code = st[0]
        if code in "RC":
            res[toks[i + 2]] = (code, toks[i + 1])
            i += 3
        else:
            res[toks[i + 1]] = (code, None)
            i += 2
    return res


# --------------------------------------------------------------------------------------
# data


@dataclass
class FileRec:
    path: str
    status: str               # A M D R C T
    old_path: str | None
    add: int                  # lines, whitespace-only changes ignored (-w) unless ws_sensitive
    dele: int
    add_raw: int
    del_raw: int
    binary: bool = False
    kind: str = "code"        # code | test | docs | filtered
    reason: str = ""          # why filtered / routed
    compacted: int = 0        # removed lines shown compactly (deleted file / deletion-only hunks)
    inline_test: int = 0      # changed lines inside Rust #[cfg(test)] modules (counted as tests)
    test_ranges: dict = field(default_factory=dict)  # {"head": [[s, e]], "base": [[s, e]]}
    unit: str = ""
    tests: dict = field(default_factory=dict)   # {"added": [...], "changed": [...], "removed": [...]}

    @property
    def lines(self) -> int:
        return self.add + self.dele

    @property
    def effective(self) -> float:
        if self.kind == "code":
            return max(self.lines - self.compacted - self.inline_test, 0) + TEST_WEIGHT * self.inline_test
        if self.kind == "test":
            return TEST_WEIGHT * self.lines
        return 0.0


@dataclass
class Unit:
    uid: str
    name: str
    files: list[str]
    effective: float
    code_files: int = 0
    test_files: int = 0
    docs_files: list[str] = field(default_factory=list)
    lanes: list[str] = field(default_factory=list)
    low_risk: bool = False       # every code file matches a config `low_risk` glob: fewer reviewers, run last
    promoted: str = ""           # why triage gave a low-risk unit the full review anyway
    status: str = "planned"
    note: str = ""


# --------------------------------------------------------------------------------------
# classification


def classify_files(wt: Path, mb: str, head: str, cfg: dict, paths: list[str] | None) -> list[FileRec]:
    ns = _parse_name_status_z(git(wt, "diff", "--name-status", "-z", "-M", "-C", mb, head))
    raw = _parse_numstat_z(git(wt, "diff", "--numstat", "-z", "-M", "-C", mb, head))
    ws = _parse_numstat_z(git(wt, "diff", "--numstat", "-z", "-M", "-C", "-w", mb, head))
    ignore = list(cfg.get("ignore", []))
    test_globs = TEST_GLOBS + list(cfg.get("test_globs", []))
    always = list(cfg.get("always_review", []))
    recs: list[FileRec] = []
    for path, (st, old) in sorted(ns.items()):
        a_raw, d_raw, _ = raw.get(path, (None, None, None))
        if ws_sensitive(path):
            a, d = a_raw, d_raw   # whitespace can change what the file does: count it
        else:
            # `--numstat -w` leaves out a file whose changes are all whitespace: it changed 0 lines
            a, d, _ = ws.get(path, (0, 0, None))
        binary = a_raw is None
        r = FileRec(path=path, status=st, old_path=old, add=a or 0, dele=d or 0,
                    add_raw=a_raw or 0, del_raw=d_raw or 0, binary=binary)
        name = PurePosixPath(path).name
        ext = PurePosixPath(path).suffix.lower()
        hit_ignore = glob_match(path, ignore)
        hit_gen = glob_match(path, GENERATED_GLOBS)
        # `always_review` (model prompts, allowlists, vendored code the repo wants reviewed): code, whatever
        # the lockfile, ignore, generated, docs and test rules say; binary and whitespace-only files stay out
        keep = glob_match(path, always) is not None
        if paths and not glob_match(path, paths) and not (old and glob_match(old, paths)):
            r.kind, r.reason = "filtered", "outside --paths"
        elif name in LOCKFILE_NAMES and not (keep and not binary):
            r.kind, r.reason = "filtered", "lockfile"
        elif binary:
            r.kind, r.reason = "filtered", "binary"
        elif hit_ignore and not keep:
            r.kind, r.reason = "filtered", f"repo ignore glob `{hit_ignore}`"
        elif hit_gen and not keep:
            r.kind, r.reason = "filtered", f"generated/vendored (`{hit_gen}`)"
        elif ((ext in DOC_EXTS and not NOT_DOCS.search(path)) or name in DOC_NAMES) and not keep:
            r.kind, r.reason = "docs", "docs lane only"
        elif glob_match(path, test_globs) and not keep:
            r.kind, r.reason = "test", "test file: listed, not diffed"
        elif r.add == 0 and r.dele == 0 and (r.add_raw or r.del_raw):
            r.kind, r.reason = "filtered", "whitespace-only change"
        elif st == "D":
            r.reason = "deleted file: shown compactly"
            r.compacted = r.dele
        recs.append(r)
    return recs


# --------------------------------------------------------------------------------------
# test functions


_RUST_TEST_ATTR = re.compile(r"^\s*#\[\s*(?:\w+::)*(test|rstest|test_case|quickcheck|wasm_bindgen_test)\b")
_RUST_FN = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(\w+)")
_TEST_PATTERNS = {
    ".sol": re.compile(r"\bfunction\s+((?:test|testFuzz|testFail|invariant)\w*)\s*\("),
    ".py": re.compile(r"^\s*(?:async\s+)?def\s+(test\w*)\s*\("),
    ".go": re.compile(r"^func\s+((?:Test|Fuzz)\w+)\s*\("),
}
_JS_TEST = re.compile(r"""^\s*(?:it|test)(?:\.(?:only|skip|concurrent|todo))?\s*\(\s*(['"`])(.*?)\1""")
_JS_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}


def test_defs(text: str, ext: str) -> list[tuple[str, int]]:
    """(name, 1-based line) of test functions in a file."""
    out: list[tuple[str, int]] = []
    lines = text.splitlines()
    if ext == ".rs":
        pending = -1
        for i, ln in enumerate(lines):
            if _RUST_TEST_ATTR.match(ln):
                pending = i
                continue
            m = _RUST_FN.match(ln)
            if m and pending >= 0 and i - pending <= 6:
                out.append((m.group(1), i + 1))
                pending = -1
            elif m:
                pending = -1
    elif ext in _JS_EXTS:
        for i, ln in enumerate(lines):
            m = _JS_TEST.match(ln)
            if m:
                out.append((m.group(2)[:80], i + 1))
    elif ext in _TEST_PATTERNS:
        for i, ln in enumerate(lines):
            m = _TEST_PATTERNS[ext].search(ln)
            if m:
                out.append((m.group(1), i + 1))
    return out


def _blobs(wt: Path, specs: list[str]) -> dict[str, str]:
    """Read many `rev:path` blobs in one git process. Missing ones map to ''."""
    # One spec per line: a path with a newline would shift every later reply onto the wrong spec (and
    # git drops a trailing CR). Such paths are left out, so they map to '' too.
    specs = [s for s in specs if "\n" not in s and "\r" not in s]
    if not specs:
        return {}
    r = subprocess.run(["git", "-C", str(wt), "cat-file", "--batch"], input=("\n".join(specs) + "\n").encode(),
                       capture_output=True)
    out, data, pos = {}, r.stdout, 0
    for spec in specs:
        nl = data.find(b"\n", pos)
        if nl < 0:
            break
        header = data[pos:nl].decode(errors="replace")
        pos = nl + 1
        if header.endswith("missing") or " " not in header:
            out[spec] = ""
            continue
        size = int(header.rsplit(" ", 1)[1])
        out[spec] = data[pos:pos + size].decode(errors="replace")
        pos += size + 1
    return out


_CFG_TEST = re.compile(r"^\s*#\[cfg\(\s*test\s*\)\]")
_MOD_OPEN = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+\w+\s*\{")


def rust_test_ranges(text: str) -> list[list[int]]:
    """1-based inclusive line ranges of `#[cfg(test)] mod x { ... }` blocks (brace counting;
    braces inside strings or comments can make a range end early or late, so a range that does
    not close cleanly is left out)."""
    lines = text.splitlines()
    out, i = [], 0
    while i < len(lines):
        if _CFG_TEST.match(lines[i]):
            j = i + 1
            while j < len(lines) and lines[j].lstrip().startswith("#["):
                j += 1
            if j < len(lines) and _MOD_OPEN.match(lines[j]):
                depth, k = 0, j
                while k < len(lines):
                    depth += lines[k].count("{") - lines[k].count("}")
                    if depth <= 0 and k > j or (depth == 0 and k == j and "}" in lines[k]):
                        break
                    k += 1
                end = min(k + 1, len(lines))
                # A range that runs past the module would hide the code below it as test code. Keep
                # it only if it ends with a `}` at the `mod` line's indentation and nothing inside is
                # indented no deeper than that line (a module's body is indented); otherwise the
                # module stays in the diff.
                ind = _indent(lines[j])
                if lines[end - 1].lstrip().startswith("}") and _indent(lines[end - 1]) == ind and \
                        not any(ln.strip() and _indent(ln) <= ind for ln in lines[j + 1:end - 1]):
                    out.append([i + 1, end])
                i = k + 1
                continue
        i += 1
    return out


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _in(ranges: list[list[int]], ln: int) -> bool:
    return any(a <= ln <= b for a, b in ranges)


def _hunk_lines(wt: Path, mb: str, head: str, rec: "FileRec") -> tuple[list[int], list[int]]:
    """(head line numbers of added lines, base line numbers of removed lines), -w."""
    specs = [rec.path] + ([rec.old_path] if rec.old_path else [])
    d = git(wt, "diff", "-U0", "-w", "-M", "--no-color", mb, head, "--", *specs, check=False)
    plus, minus = [], []
    o = n = 0
    for ln in d.splitlines():
        m = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", ln)
        if m:
            o, n = int(m.group(1)), int(m.group(2))
            continue
        if ln.startswith("+++") or ln.startswith("---"):
            continue
        if ln.startswith("+"):
            plus.append(n)
            n += 1
        elif ln.startswith("-"):
            minus.append(o)
            o += 1
    return plus, minus


def _changed_head_lines(wt: Path, mb: str, head: str, path: str) -> list[int]:
    d = git(wt, "diff", "-U0", "-w", "--no-color", mb, head, "--", path, check=False)
    lines = []
    for m in re.finditer(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", d, re.M):
        start, cnt = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
        lines.extend(range(start, start + cnt) if cnt else [max(start, 1)])
    return lines


def collect_tests(wt: Path, mb: str, head: str, recs: list[FileRec]) -> None:
    """Fill rec.tests for test files, and for Rust code files with inline #[test] functions."""
    cands = [r for r in recs if r.kind == "test" or (r.kind == "code" and r.path.endswith(".rs"))]
    cands = [r for r in cands if PurePosixPath(r.path).suffix.lower() in (".rs", ".sol", ".py", ".go", *_JS_EXTS)]
    specs = []
    for r in cands:
        if r.status != "D":
            specs.append(f"{head}:{r.path}")
        if r.status != "A":
            specs.append(f"{mb}:{r.old_path or r.path}")
    blobs = _blobs(wt, specs)
    for r in cands:
        ext = PurePosixPath(r.path).suffix.lower()
        head_txt = blobs.get(f"{head}:{r.path}", "")
        base_txt = blobs.get(f"{mb}:{r.old_path or r.path}", "")
        hdefs, bdefs = test_defs(head_txt, ext), test_defs(base_txt, ext)
        if r.kind == "code" and ext == ".rs":
            hr, br = rust_test_ranges(head_txt), rust_test_ranges(base_txt)
            if hr or br:
                r.test_ranges = {"head": hr, "base": br}
                plus, minus = _hunk_lines(wt, mb, head, r)
                r.inline_test = min(sum(_in(hr, x) for x in plus) + sum(_in(br, x) for x in minus), r.lines)
        if not hdefs and not bdefs:
            continue
        hnames, bnames = [n for n, _ in hdefs], {n for n, _ in bdefs}
        added = [n for n in hnames if n not in bnames]
        removed = sorted(bnames - set(hnames))
        changed: list[str] = []
        if hdefs and r.status != "A":
            # a test spans from its definition to the next definition indented no deeper than it
            hl = head_txt.splitlines()

            def indent(x: str) -> int:
                return len(x) - len(x.lstrip())
            defs_at = [(i + 1, indent(ln)) for i, ln in enumerate(hl) if _RUST_FN.match(ln) or _DEF_RE.match(ln)]
            test_at = {s for _, s in hdefs}
            ends = {}
            for _, s in hdefs:
                ind = indent(hl[s - 1]) if s - 1 < len(hl) else 0
                ends[s] = next((b for b, bi in defs_at if b > s and bi <= ind), None) or \
                    next((t for t in sorted(test_at) if t > s), 10 ** 9)
            for ln in _changed_head_lines(wt, mb, head, r.path):
                for n, s in hdefs:
                    if s <= ln < ends[s] and n not in added and n not in changed:
                        changed.append(n)
        if added or changed or removed:
            r.tests = {"added": added, "changed": changed, "removed": removed}


def tests_block(recs: list[FileRec], limit_names: int = 400) -> str:
    """Prompt text listing test files and the test functions they add/change/remove."""
    tfiles = [r for r in recs if r.kind == "test"]
    inline = [r for r in recs if r.kind == "code" and r.tests]
    if not tfiles and not inline:
        return "No test files changed."
    out = [f"Changed test files ({len(tfiles)}):"] if tfiles else ["No test files changed. Inline test functions in changed source files:"]
    shown = 0
    for r in tfiles + inline:
        t = r.tests
        tag = "" if r.kind == "test" else " (inline tests in a source file; its diff is above)"
        st = {"A": "added", "D": "deleted", "R": "renamed", "C": "copied"}.get(r.status, "modified")
        out.append(f"- {r.path} ({st}, +{r.add}/-{r.dele}){tag}")
        for k in ("added", "changed", "removed"):
            names = t.get(k) or []
            if names and shown < limit_names:
                take = names[: max(limit_names - shown, 0)]
                shown += len(take)
                more = f" (+{len(names) - len(take)} more)" if len(take) < len(names) else ""
                out.append(f"    {k}: {', '.join(take)}{more}")
    return "\n".join(out)


# --------------------------------------------------------------------------------------
# diff rendering


_DEF_RE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:const\s+)?"
                     r"(fn|struct|enum|trait|impl|mod|function|contract|interface|library|modifier|event|"
                     r"def|class|func|type)\s+([A-Za-z_][\w<>, ]*)")


def _defs_in(lines: list[str]) -> list[str]:
    out = []
    for ln in lines:
        m = _DEF_RE.match(ln)
        if m:
            nm = f"{m.group(1)} {m.group(2).split('<')[0].split(' ')[0]}"
            if nm not in out:
                out.append(nm)
    return out


def compact_deletions(diff: str) -> tuple[str, dict[str, int]]:
    """Replace the body of deleted files and of large deletion-only hunks with a one-line
    summary (count + removed definitions). Returns (diff, {path: removed lines compacted})."""
    out, saved = [], {}
    chunks = re.split(r"(?m)^(?=diff --git )", diff)
    for ch in chunks:
        if not ch.startswith("diff --git "):
            out.append(ch)
            continue
        head_end = ch.find("\n@@")
        if head_end < 0:
            out.append(ch)
            continue
        header, body = ch[:head_end + 1], ch[head_end + 1:]
        m = re.search(r"^\+\+\+ b/(.*)$|^--- a/(.*)$", header, re.M)
        path = (m.group(1) or m.group(2)) if m else "?"
        deleted_file = "\ndeleted file mode" in header
        hunks = re.split(r"(?m)^(?=@@ )", body)
        new_body = []
        for h in hunks:
            if not h.startswith("@@"):
                new_body.append(h)
                continue
            hl = h.split("\n")
            minus = [x[1:] for x in hl[1:] if x.startswith("-")]
            plus = [x for x in hl[1:] if x.startswith("+")]
            if minus and not plus and (deleted_file or len(minus) >= DELETION_COMPACT_MIN):
                defs = _defs_in(minus)
                what = "file deleted" if deleted_file else "deletion-only hunk"
                summary = (f"{hl[0]}\n[{what}: {len(minus)} removed lines not shown"
                           + (f"; removed definitions: {', '.join(defs[:40])}" + (" ..." if len(defs) > 40 else "")
                              if defs else "") + "]\n")
                new_body.append(summary)
                saved[path] = saved.get(path, 0) + len(minus)
            else:
                new_body.append(h)
        out.append(header + "".join(new_body))
    return "".join(out), saved


def strip_test_modules(diff: str, ranges: dict[str, dict]) -> str:
    """Drop diff lines inside Rust #[cfg(test)] modules (head ranges for +/context lines,
    base ranges for - lines); each dropped run becomes one marker line."""
    if not ranges:
        return diff
    out = []
    for ch in re.split(r"(?m)^(?=diff --git )", diff):
        m = re.search(r"^\+\+\+ b/(.*)$", ch, re.M) or re.search(r"^--- a/(.*)$", ch, re.M)
        rg = ranges.get(m.group(1)) if m else None
        if not rg:
            out.append(ch)
            continue
        hr, br = rg.get("head", []), rg.get("base", [])
        res, o, n, dropped, in_hunk = [], 0, 0, 0, False
        for ln in ch.split("\n"):
            hm = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", ln)
            if hm:
                if dropped:
                    res.append(f"[{dropped} lines of a #[cfg(test)] module omitted; tests are listed in <tests>]")
                    dropped = 0
                o, n, in_hunk = int(hm.group(1)), int(hm.group(2)), True
                res.append(ln)
                continue
            if not in_hunk or ln.startswith("\\"):
                res.append(ln)
                continue
            tag = ln[:1]
            if tag == "+":
                drop, n = _in(hr, n), n + 1
            elif tag == "-":
                drop, o = _in(br, o), o + 1
            else:
                drop = _in(hr, n)
                o, n = o + 1, n + 1
            if drop:
                dropped += 1
                continue
            if dropped:
                res.append(f"[{dropped} lines of a #[cfg(test)] module omitted; tests are listed in <tests>]")
                dropped = 0
            res.append(ln)
        if dropped:
            res.append(f"[{dropped} lines of a #[cfg(test)] module omitted; tests are listed in <tests>]")
        out.append("\n".join(res))
    return "".join(out)


def _pathspecs(recs: list[FileRec]) -> list[str]:
    specs = []
    for r in recs:
        specs.append(f":(literal){r.path}")
        if r.old_path:
            specs.append(f":(literal){r.old_path}")
    return specs


def _git_diff(wt: Path, args: list[str], mb: str, head: str, recs: list[FileRec], attrs: Path) -> str:
    """`git diff <args>` of recs: with -w, and in a second call without it for whitespace-sensitive files."""
    out = ""
    for flag in (["-w"], []):
        rs = [r for r in recs if _ws_flag(r.path) == flag]
        if rs:
            out += git(wt, *args, *flag, mb, head, "--", *_pathspecs(rs), attrs=attrs)
    return out


def seed_diff(wt: Path, mb: str, head: str, recs: list[FileRec], attrs: Path,
              cap: int = DIFF_CAP, cfg: dict | None = None,
              omitted_out: list[str] | None = None) -> tuple[str, str, dict[str, int]]:
    """Whole-function diff of code files (-w -M -C -W; no -w for whitespace-sensitive files),
    deletion-only hunks compacted.
    Falls back to -U20, then ranking, above `cap` chars. Files the ranked seed leaves out are
    appended to `omitted_out` when given."""
    code = [r for r in recs if r.kind == "code"]
    if not code:
        return "", "no code files in this unit", {}
    base = ["diff", "--no-color", "--no-ext-diff", "-M", "-C"]
    ws = "whitespace ignored (not in Python, shell, YAML or Makefiles)"
    tr = {r.path: r.test_ranges for r in code if r.test_ranges}
    diff, saved = compact_deletions(strip_test_modules(_git_diff(wt, [*base, "-W"], mb, head, code, attrs), tr))
    mode = f"-W (whole function context), {ws}, renames/copies detected"
    if len(diff) > cap:
        diff, saved = compact_deletions(strip_test_modules(_git_diff(wt, [*base, "-U20"], mb, head, code, attrs), tr))
        mode = f"-U20, {ws} (the -W diff exceeded {cap:,} chars)"
    if len(diff) > cap:
        diff, saved, omitted = _ranked_diff(wt, base, mb, head, code, tr, attrs, cap, cfg or {})
        if omitted_out is not None:
            omitted_out.extend(omitted)
        mode = (f"ranked to fit {cap:,} chars: larger changes first, low-risk files last, whole functions "
                f"where they fit, shorter context otherwise; {len(omitted)} file(s) not shown, listed "
                f"at the end (read them with your tools)")
    return diff, mode, saved


def is_low_risk(path: str, cfg: dict) -> bool:
    """A path matched by a config `low_risk` glob. Everything else gets the full review: an unlisted
    path (a new directory, a file nobody classified) is never low-risk."""
    return glob_match(path, list(cfg.get("low_risk", []))) is not None


def _rank_key(r: FileRec, cfg: dict) -> tuple:
    return (is_low_risk(r.path, cfg), -r.lines, r.path)


def _ranked_diff(wt: Path, base: list[str], mb: str, head: str, code: list[FileRec], tr: dict,
                 attrs: Path, cap: int, cfg: dict) -> tuple[str, dict[str, int], list[str]]:
    """Per-file diffs, larger changes first and low-risk files last, until `cap`: -W, else -U20, else -U3,
    else the -U3 diff cut where the room ends; the rest listed."""
    parts: list[str] = []
    saved_all: dict[str, int] = {}
    omitted: list[str] = []
    used = 0
    for r in sorted(code, key=lambda r: _rank_key(r, cfg)):
        placed = False
        for ctx in ("-W", "-U20", "-U3"):
            d, saved = compact_deletions(strip_test_modules(
                git(wt, *base, *_ws_flag(r.path), ctx, mb, head, "--", *_pathspecs([r]), attrs=attrs), tr))
            if used + len(d) <= cap:
                parts.append(d); used += len(d); saved_all.update(saved); placed = True
                break
        if not placed:
            # too big even at -U3: show as much of it as fits (`d` is its -U3 diff) rather than nothing
            room = cap - used
            if room > 10_000 and d:
                keep = d[: room - 300]
                keep = keep[: keep.rfind("\n") + 1]
                note = (f"[... the diff of {r.path} is cut here: {len(d) - len(keep):,} more chars; "
                        f"read the rest with your tools]\n")
                parts.append(keep + note); used += len(keep) + len(note); saved_all.update(saved)
            else:
                omitted.append(r.path)
    diff = "".join(parts)
    if omitted:
        shown = ", ".join(omitted[:200]) + (" ..." if len(omitted) > 200 else "")
        diff += f"\n[... {len(omitted)} changed file(s) not shown in this seed; read them with your tools: {shown}]\n"
    return diff, saved_all, omitted


def plain_diff(wt: Path, mb: str, head: str, recs: list[FileRec], cap: int = DIFF_CAP) -> tuple[str, str]:
    """`git diff -U3` of every file in the unit except lockfiles/binary/outside --paths:
    what a plain static reviewer sees (tests, docs, generated files included)."""
    keep = [r for r in recs if not (r.kind == "filtered" and r.reason in ("lockfile", "binary", "outside --paths"))]
    if not keep:
        return "", "empty"
    diff = git(wt, "diff", "--no-color", "--no-ext-diff", "-U3", mb, head, "--", *_pathspecs(keep))
    note = "plain -U3 (lockfiles excluded)"
    if len(diff) > cap:
        cut = diff.rfind("\n", 0, cap)
        diff = diff[:cut] + "\n[... diff truncated ...]\n"
        note += f"; TRUNCATED to {cap:,} chars"
    return diff, note


def docs_diff(wt: Path, mb: str, head: str, recs: list[FileRec], attrs: Path, cap: int = DIFF_CAP) -> str:
    docs = [r for r in recs if r.kind == "docs"]
    if not docs:
        return ""
    d = git(wt, "diff", "--no-color", "--no-ext-diff", "-w", "-M", "-C", "-U5", "-B", mb, head, "--",
            *_pathspecs(docs), attrs=attrs)
    if len(d) > cap:
        d = d[: d.rfind("\n", 0, cap)] + "\n[... docs diff truncated ...]\n"
    return d


def changed_symbols(wt: Path, mb: str, head: str, recs: list[FileRec], attrs: Path, per_file: int = 25) -> dict[str, list[str]]:
    """File -> function/definition names touched (hunk headers + added definitions). For the PR map."""
    code = [r for r in recs if r.kind == "code"]
    if not code:
        return {}
    d = _git_diff(wt, ["diff", "--no-color", "-M", "-C", "-U0"], mb, head, code, attrs)
    out: dict[str, list[str]] = {}
    cur = None
    for ln in d.splitlines():
        if ln.startswith("+++ b/"):
            cur = ln[6:]
            out.setdefault(cur, [])
        elif ln.startswith("@@") and cur:
            ctx = ln.split("@@", 2)[-1].strip()
            if ctx:
                defs = _defs_in([ctx]) or [ctx[:60]]
                for x in defs:
                    if x not in out[cur]:
                        out[cur].append(x)
        elif ln.startswith("+") and cur and not ln.startswith("+++"):
            for x in _defs_in([ln[1:]]):
                if x not in out[cur]:
                    out[cur].append(x)
    return {k: v[:per_file] for k, v in out.items()}


# --------------------------------------------------------------------------------------
# units


def size_class(effective: float) -> str:
    return "normal" if effective <= MAX_EFFECTIVE else "too big"


def plan_units(recs: list[FileRec], cfg: dict) -> list[Unit]:
    """One unit with every code and test file of the PR (docs listed with it). It is low-risk only if
    every code file matches a config `low_risk` glob."""
    reviewable = [r for r in recs if r.kind in ("code", "test")]
    code = [r for r in reviewable if r.kind == "code"]
    u = Unit(uid="u01", name="whole PR", files=[r.path for r in reviewable],
             effective=sum(r.effective for r in reviewable), code_files=len(code),
             test_files=len(reviewable) - len(code),
             low_risk=bool(code) and all(is_low_risk(r.path, cfg) for r in code))
    for r in reviewable:
        r.unit = u.uid
    u.docs_files = [r.path for r in recs if r.kind == "docs"]
    return [u]


# --------------------------------------------------------------------------------------
# malicious-code check: code aimed at the people and machines that build, test or open the repository
# (2026-11-17). The finders do not see test diffs, and nobody reviews lockfiles, so this looks at what
# runs on a developer's machine or in CI by itself: tests, build and install scripts, hooks, editor
# tasks, dependency sources.

DEV_EXEC_GLOBS = [
    "**/package.json", "**/.npmrc", "**/.yarnrc*", "**/Makefile", "**/makefile", "**/*.mk", "**/justfile",
    "**/Justfile", "**/build.rs", "**/conftest.py", "**/setup.py", "**/setup.cfg", "**/pyproject.toml",
    "**/test_helper.exs", "**/mix.exs", "**/foundry.toml", "**/Cargo.toml", "**/.cargo/config*", "**/.vscode/**",
    "**/.devcontainer/**", "**/.husky/**", "**/.githooks/**", "**/lefthook.y*ml", "**/.pre-commit-config.yaml",
    "**/.envrc", "**/.github/workflows/**", "**/.github/actions/**", "**/scripts/**", "**/*.sh",
]
# (name, pattern, where): "all" = any changed file, "dev" = tests and DEV_EXEC_GLOBS files only. A hit is a
# pointer for the model, not a verdict: most are harmless.
MAL_PATTERNS = [
    ("hidden unicode", re.compile("[‪-‮⁦-⁩​-‍⁠﻿]"), "all"),
    ("download and run", re.compile(r"(curl|wget|Invoke-WebRequest|\biwr\b)[^\n|]*\|\s*(ba|z)?sh\b|\|\s*iex\b", re.I), "all"),
    ("credential files", re.compile(r"\.ssh\b|id_(rsa|ed25519|ecdsa)|\.aws/|\.config/gcloud|\.git-credentials|"
                                    r"\.docker/config|\.kube/config|\.netrc|\.gnupg|\.npmrc\b", re.I), "all"),
    ("secret env vars", re.compile(r"(process\.env|System\.get_env|std::env::var|os\.environ|os\.getenv|ENV\[)"
                                   r"[^\n]{0,60}(TOKEN|SECRET|PASSW|_KEY|AWS_|NPM_|GITHUB_|GH_)", re.I), "all"),
    ("whole environment", re.compile(r"\benv\s*\||printenv|os\.environ\b(?!\s*[\[.(])|System\.get_env\(\s*\)|"
                                     r"process\.env\b(?!\s*[\[.])"), "dev"),
    ("runs commands", re.compile(r"child_process|execSync|spawnSync|\bexecFile\b|System\.cmd|:os\.cmd|Port\.open|"
                                 r"std::process::Command|subprocess\.|os\.system|\bvm\.ffi\b|\beval\(|new Function\(|"
                                 r"Runtime\.getRuntime"), "dev"),
    ("decodes data", re.compile(r"Base\.decode64|base64\s+(-d|--decode)|atob\(|b64decode|,\s*['\"]base64['\"]\s*\)",
                                re.I), "all"),
    ("long encoded blob", re.compile(r"[A-Za-z0-9+/=]{200,}"), "all"),
    ("install script", re.compile(r'"(pre|post)?install"\s*:|"prepare"\s*:|"prepublish(Only)?"\s*:'), "all"),
    ("runs by itself", re.compile(r'"runOn"\s*:\s*"folderOpen"|\bffi\s*=\s*true|ignore-scripts\s*=\s*false'), "all"),
    ("dependency source", re.compile(r'git\+(https?|ssh)://|"github:|"resolved"\s*:\s*"https?://(?!registry\.(npmjs\.org|'
                                     r'yarnpkg\.com))|tarball:\s*https?://(?!registry\.npmjs\.org)|source = "git\+|'
                                     r'https?://[^\s"\']+\.(tgz|tar\.gz)\b'), "all"),
    ("network host", re.compile(r"https?://(?!localhost\b|127\.0\.0\.1|\[::1\]|0\.0\.0\.0)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "dev"),
]
# chars of diff for the check (tests and dev files; files with hits first). One direct API call, so Codex's
# compaction does not apply; DeepSeek Flash takes 1M tokens, and 600k chars cost about a cent.
MAL_INPUT_CAP = 600_000


def is_dev_exec(path: str) -> bool:
    return glob_match(path, DEV_EXEC_GLOBS) is not None


def malicious_scan(wt: Path, mb: str, head: str, recs: list[FileRec], limit: int = 300) -> list[dict]:
    """Added lines that match MAL_PATTERNS, in every changed text file but docs (lockfiles included)."""
    kinds = {r.path: r.kind for r in recs}
    paths = [r.path for r in recs if r.kind != "docs" and not r.binary and r.reason != "outside --paths"]
    if not paths:
        return []
    keep = set(paths)
    diff = git(wt, "diff", "--no-color", "--no-ext-diff", "-U0", "-M", mb, head, "--", *_pathspecs(
        [r for r in recs if r.path in keep and not _ODD_NAME.search(r.path)]))
    hits, path, line = [], None, 0
    for ln in diff.splitlines():
        if ln.startswith("+++ "):
            path = ln[6:] if ln.startswith("+++ b/") else None
            continue
        m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", ln)
        if m:
            line = int(m.group(1))
            continue
        if path is None or not ln.startswith("+"):
            continue
        dev = kinds.get(path) == "test" or is_dev_exec(path)
        for name, rx, where in MAL_PATTERNS:
            if (where == "all" or dev) and rx.search(ln[1:]):
                hits.append({"file": path, "line": line, "kind": name, "text": ln[1:].strip()[:200]})
        line += 1
        if len(hits) >= limit:
            break
    for r in recs:   # files that run by themselves just by being there
        if glob_match(r.path, ["**/.vscode/tasks.json", "**/.husky/**", "**/.githooks/**", "**/.devcontainer/**",
                               "**/lefthook.y*ml", "**/.pre-commit-config.yaml", "**/.envrc"]) and r.status != "D":
            hits.append({"file": r.path, "line": 0, "kind": "runs by itself", "text": "file changed: " + r.path})
    return hits[:limit]


def malicious_input(wt: Path, mb: str, head: str, recs: list[FileRec], attrs: Path) -> tuple[str, list[dict]]:
    """The diff the malicious-code check reads (every changed file but docs and lockfiles, which it sees only
    through their pattern hits; files with a hit first, then tests and files that run by themselves) and
    the pattern hits. ("", []) when only product code changed and nothing matched."""
    hits = malicious_scan(wt, mb, head, recs)
    hit_files = {h["file"] for h in hits}
    # the whole diff, so a harmful helper in product code called from an innocent-looking test line is seen
    # too: files with a hit first, then tests and files that run by themselves, then the rest
    pick = [r for r in recs if not r.binary and r.kind != "docs" and not _ODD_NAME.search(r.path) and r.reason not in (
        "lockfile", "outside --paths")]
    if not any(r.kind == "test" or is_dev_exec(r.path) for r in pick) and not hits:
        return "", hits   # only product code changed: the security reviewers have it
    pick.sort(key=lambda r: (r.path not in hit_files, not (r.kind == "test" or is_dev_exec(r.path)), r.path))
    out, used = [], 0
    for r in pick:
        d = git(wt, "diff", "--no-color", "--no-ext-diff", "-U3", "-M", mb, head, "--", *_pathspecs([r]), attrs=attrs)
        if used + len(d) > MAL_INPUT_CAP:
            out.append(f"[... {r.path}: diff not shown (over {MAL_INPUT_CAP:,} chars); read it in the checkout]\n")
            continue
        out.append(d)
        used += len(d)
    return "".join(out), hits


# --------------------------------------------------------------------------------------
# summaries


def filtered_table(recs: list[FileRec]) -> list[dict]:
    rows = []
    for r in recs:
        if r.kind in ("filtered", "docs", "test") or r.compacted:
            rows.append({"file": r.path, "lines": f"+{r.add_raw}/-{r.del_raw}" if not r.binary else "binary",
                         "kind": r.kind, "reason": r.reason or "deletion-only hunks compacted",
                         "compacted": r.compacted, "unit": r.unit})
    return rows


_ODD_NAME = re.compile("[\t\n\r\ufffd]")   # U+FFFD: a path that is not UTF-8 (git output is read with "replace")


def not_reviewed(recs: list[FileRec], changed: int | None = None) -> dict:
    """Changed files whose changes no reviewer sees, for the PR comment: file names the plan cannot
    handle (tab, newline, CR, not UTF-8), files git treats as binary (attributes, a NUL byte),
    whitespace-only changes, and code or test files placed in no unit. `changed`: how many paths git
    lists; paths missing from `recs` (two names that are not UTF-8 read as one) count as "unlisted"."""
    files = []
    for r in recs:
        if r.reason == "outside --paths":
            continue
        why = ("unusual file name" if _ODD_NAME.search(r.path)
               else "binary to git" if r.reason == "binary"
               else "whitespace-only change" if r.reason == "whitespace-only change"
               else "not placed in a part" if r.kind in ("code", "test") and not r.unit
               else "")
        if why:
            files.append({"file": r.path, "why": why})
    return {"files": files, "unlisted": max((changed or 0) - len(recs), 0)}


def sizes(recs: list[FileRec]) -> dict:
    raw = sum(r.add_raw + r.del_raw for r in recs)
    by_kind: dict[str, int] = {}
    for r in recs:
        by_kind[r.kind] = by_kind.get(r.kind, 0) + (r.add_raw + r.del_raw)
    code_ws = sum(r.lines for r in recs if r.kind == "code")
    code_raw = sum(r.add_raw + r.del_raw for r in recs if r.kind == "code")
    compacted = sum(r.compacted for r in recs if r.kind == "code")
    test = sum(r.lines for r in recs if r.kind == "test")
    inline = sum(r.inline_test for r in recs if r.kind == "code")
    eff = sum(r.effective for r in recs)
    return {"files": len(recs), "raw_lines": raw, "by_kind_raw": by_kind,
            "code_lines_raw": code_raw, "code_lines_ws": code_ws, "whitespace_ignored": code_raw - code_ws,
            "compacted_deleted_lines": compacted, "test_lines": test, "inline_test_lines": inline, "effective": eff,
            "class": size_class(eff)}


__all__ = ["FileRec", "Unit", "classify_files", "collect_tests", "tests_block", "seed_diff", "plain_diff",
           "docs_diff", "changed_symbols", "plan_units", "filtered_table", "not_reviewed", "sizes", "size_class",
           "glob_match", "is_low_risk", "malicious_scan", "malicious_input", "ws_sensitive", "ATTRS", "MAX_EFFECTIVE", "TEST_WEIGHT", "DIFF_CAP"]
