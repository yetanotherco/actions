"""Deterministic seed construction for the AI review pilot (no LLM calls, stdlib only).

From a checkout at the PR head and the merge-base it builds a *plan*:
  - every changed file classified as code / test / docs / filtered, with line counts
    (whitespace-only changes ignored, renames and copies detected);
  - what was filtered and why (lockfile, generated/vendored, repo ignore glob, binary,
    whitespace-only, outside --paths), for the coverage table;
  - test files and the test functions they add / change / remove (tests are not put in the
    seed diff; the agentic lanes can read them in the checkout);
  - the effective size (code lines + 0.5 x test lines, after trimming) and the size class;
  - review units for large PRs (split by package / area, sensitive units marked).
Per unit it renders the seed diff (whole-function context, deletion-only hunks compacted)
and the plain `-U3` diff used by the static-diff comparison mode.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

# 2026-11-12 (from a sizing analysis): was 20000, set before units existed. Above ~2.5-4k
# effective lines the diff no longer fits DIFF_CAP and the models do not explore more to make up for it.
# 1-3% of PRs are bigger (ethrex / lambda_vm, last 12 months).
NORMAL_MAX = 4000       # effective lines: up to this, one unit; above it, the PR is split into units
LARGE_MAX = 20000       # "large" up to this, "huge" above (a label only: both split, both under --pr-budget)
UNIT_TARGET = 2500      # small areas are packed together up to about this size
UNIT_MAX = 4000         # an area bigger than this is split one directory level deeper, then by files
TEST_WEIGHT = 0.5
DIFF_CAP = 250_000      # chars of diff in the prompt (was 150k: 13-25% of large-PR units lost files)
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
MANIFESTS = {"Cargo.toml", "package.json", "foundry.toml", "go.mod", "pyproject.toml", "setup.py"}

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
        parts = t.split("\t")
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
    add: int                  # lines, whitespace-only changes ignored (-w)
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
    sensitive: bool
    sensitive_hits: list[str]
    effective: float
    code_files: int = 0
    test_files: int = 0
    docs_files: list[str] = field(default_factory=list)
    lanes: list[str] = field(default_factory=list)
    priority: int = 999          # index of the first config `priority` glob any file matches
    sensitive_lines: float = 0.0  # effective lines in files that match a sensitive glob
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
    recs: list[FileRec] = []
    for path, (st, old) in sorted(ns.items()):
        a_raw, d_raw, _ = raw.get(path, (None, None, None))
        a, d, _ = ws.get(path, (a_raw, d_raw, None))
        binary = a_raw is None
        r = FileRec(path=path, status=st, old_path=old, add=a or 0, dele=d or 0,
                    add_raw=a_raw or 0, del_raw=d_raw or 0, binary=binary)
        name = PurePosixPath(path).name
        ext = PurePosixPath(path).suffix.lower()
        hit_ignore = glob_match(path, ignore)
        hit_gen = glob_match(path, GENERATED_GLOBS)
        if paths and not glob_match(path, paths) and not (old and glob_match(old, paths)):
            r.kind, r.reason = "filtered", "outside --paths"
        elif name in LOCKFILE_NAMES:
            r.kind, r.reason = "filtered", "lockfile"
        elif binary:
            r.kind, r.reason = "filtered", "binary"
        elif hit_ignore:
            r.kind, r.reason = "filtered", f"repo ignore glob `{hit_ignore}`"
        elif hit_gen:
            r.kind, r.reason = "filtered", f"generated/vendored (`{hit_gen}`)"
        elif (ext in DOC_EXTS and not NOT_DOCS.search(path)) or name in DOC_NAMES:
            r.kind, r.reason = "docs", "docs lane only"
        elif glob_match(path, test_globs):
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
    braces inside strings or comments can make a range end early or late)."""
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
                out.append([i + 1, min(k + 1, len(lines))])
                i = k + 1
                continue
        i += 1
    return out


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


def seed_diff(wt: Path, mb: str, head: str, recs: list[FileRec], attrs: Path,
              cap: int = DIFF_CAP, cfg: dict | None = None,
              omitted_out: list[str] | None = None) -> tuple[str, str, dict[str, int]]:
    """Whole-function diff of code files (-w -M -C -W), deletion-only hunks compacted.
    Falls back to -U20, then ranking, above `cap` chars. Files the ranked seed leaves out are
    appended to `omitted_out` when given."""
    code = [r for r in recs if r.kind == "code"]
    if not code:
        return "", "no code files in this unit", {}
    base = ["diff", "--no-color", "--no-ext-diff", "-w", "-M", "-C"]
    tr = {r.path: r.test_ranges for r in code if r.test_ranges}
    diff, saved = compact_deletions(strip_test_modules(
        git(wt, *base, "-W", mb, head, "--", *_pathspecs(code), attrs=attrs), tr))
    mode = "-W (whole function context), whitespace ignored, renames/copies detected"
    if len(diff) > cap:
        diff, saved = compact_deletions(strip_test_modules(
            git(wt, *base, "-U20", mb, head, "--", *_pathspecs(code), attrs=attrs), tr))
        mode = f"-U20, whitespace ignored (the -W diff exceeded {cap:,} chars)"
    if len(diff) > cap:
        diff, saved, omitted = _ranked_diff(wt, base, mb, head, code, tr, attrs, cap, cfg or {})
        if omitted_out is not None:
            omitted_out.extend(omitted)
        mode = (f"ranked to fit {cap:,} chars: sensitive and priority files first, whole functions "
                f"where they fit, shorter context otherwise; {len(omitted)} file(s) not shown, listed "
                f"at the end (read them with your tools)")
    return diff, mode, saved


def file_sensitivity(path: str, cfg: dict) -> str | None:
    """Why a file is sensitive, or None. A `sensitive` glob match; otherwise, with
    `default_sensitive` (the default), any path that no `low_risk` glob covers: an unlisted path
    (a new directory, a file nobody classified) gets the full review instead of the reduced one."""
    hit = glob_match(path, list(cfg.get("sensitive", [])))
    if hit:
        return hit
    if cfg.get("default_sensitive", True) and not glob_match(path, list(cfg.get("low_risk", []))):
        return "(unlisted: sensitive by default)"
    return None


def _rank_key(r: FileRec, cfg: dict) -> tuple:
    sensitive = file_sensitivity(r.path, cfg) is not None
    prio_globs = list(cfg.get("priority", []))
    g = glob_match(r.path, prio_globs)
    return (0 if sensitive else 1, prio_globs.index(g) if g else 999, -r.lines, r.path)


def _ranked_diff(wt: Path, base: list[str], mb: str, head: str, code: list[FileRec], tr: dict,
                 attrs: Path, cap: int, cfg: dict) -> tuple[str, dict[str, int], list[str]]:
    """Per-file diffs in priority order until `cap`: -W, else -U20, else -U3; the rest listed."""
    parts: list[str] = []
    saved_all: dict[str, int] = {}
    omitted: list[str] = []
    used = 0
    for r in sorted(code, key=lambda r: _rank_key(r, cfg)):
        placed = False
        for ctx in ("-W", "-U20", "-U3"):
            d, saved = compact_deletions(strip_test_modules(
                git(wt, *base, ctx, mb, head, "--", *_pathspecs([r]), attrs=attrs), tr))
            if used + len(d) <= cap:
                parts.append(d); used += len(d); saved_all.update(saved); placed = True
                break
        if not placed:
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
    d = git(wt, "diff", "--no-color", "-w", "-M", "-C", "-U0", mb, head, "--", *_pathspecs(code), attrs=attrs)
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


def package_roots(wt: Path, head: str) -> list[str]:
    tree = git(wt, "ls-tree", "-r", "--name-only", head).splitlines()
    roots = set()
    for p in tree:
        pp = PurePosixPath(p)
        if pp.name in MANIFESTS and str(pp.parent) != ".":
            roots.add(str(pp.parent))
    return sorted(roots, key=lambda s: (-s.count("/"), s))


def _area(path: str, roots: list[str], depth: int) -> str:
    for r in roots:  # deepest first
        if path.startswith(r + "/"):
            return r
    parts = PurePosixPath(path).parts
    if len(parts) == 1:
        return "(root)"
    return "/".join(parts[: min(depth, len(parts) - 1)])


def _subarea(path: str, area: str) -> str:
    if area == "(root)":
        return area
    rest = PurePosixPath(path[len(area) + 1:]).parts
    return area if len(rest) <= 1 else f"{area}/{rest[0]}"


def size_class(effective: float) -> str:
    return "normal" if effective <= NORMAL_MAX else ("large" if effective <= LARGE_MAX else "huge")


def plan_units(wt: Path, head: str, recs: list[FileRec], cfg: dict, force_single: bool = False) -> list[Unit]:
    priority_globs = list(cfg.get("priority", []))
    reviewable = [r for r in recs if r.kind in ("code", "test")]
    eff_total = sum(r.effective for r in reviewable)

    def mk(uid: str, name: str, files: list[FileRec]) -> Unit:
        hits = sorted({h for r in files if (h := file_sensitivity(r.path, cfg))})
        prio = min([priority_globs.index(g) for r in files if (g := glob_match(r.path, priority_globs))] or [999])
        return Unit(uid=uid, name=name, files=[r.path for r in files], sensitive=bool(hits),
                    sensitive_hits=hits, effective=sum(r.effective for r in files),
                    code_files=sum(r.kind == "code" for r in files),
                    test_files=sum(r.kind == "test" for r in files), priority=prio,
                    sensitive_lines=sum(r.effective for r in files if file_sensitivity(r.path, cfg)))

    if force_single or size_class(eff_total) == "normal":
        u = mk("u01", "whole PR", reviewable)
        for r in reviewable:
            r.unit = u.uid
        u.docs_files = [r.path for r in recs if r.kind == "docs"]
        return [u]

    depth = int(cfg.get("unit_depth", 2))
    roots = package_roots(wt, head)
    code = [r for r in reviewable if r.kind == "code"]
    groups: dict[str, list[FileRec]] = {}
    for r in code:
        groups.setdefault(_area(r.path, roots, depth), []).append(r)
    # split oversize areas one level deeper, up to 3 times
    for _ in range(3):
        nxt: dict[str, list[FileRec]] = {}
        for a, fs in groups.items():
            if sum(f.effective for f in fs) > UNIT_MAX and a != "(root)":
                for f in fs:
                    nxt.setdefault(_subarea(f.path, a), []).append(f)
            else:
                nxt.setdefault(a, []).extend(fs)
        if nxt.keys() == groups.keys():
            break
        groups = nxt
    # still too big (many files directly in one directory): chunk by files in path order
    nxt = {}
    for a, fs in groups.items():
        if sum(f.effective for f in fs) <= UNIT_MAX or len(fs) == 1:
            nxt[a] = fs
            continue
        def label(ch: list[FileRec]) -> str:
            first, last = PurePosixPath(ch[0].path).name, PurePosixPath(ch[-1].path).name
            return f"{a}/{{{first}}}" if len(ch) == 1 else f"{a}/{{{first} .. {last}}}"
        chunk = []
        for f in sorted(fs, key=lambda f: f.path):
            if chunk and sum(c.effective for c in chunk) + f.effective > UNIT_TARGET:
                nxt[label(chunk)] = chunk
                chunk = []
            chunk.append(f)
        if chunk:
            nxt[label(chunk)] = chunk
    groups = nxt
    # pack small areas: same top-level directory and same sensitivity, in path order
    areas = sorted(groups)
    packed: list[tuple[str, list[FileRec]]] = []
    bins: dict[tuple[str, bool], tuple[list[str], list[FileRec]]] = {}

    def is_sens(fs):
        return any(file_sensitivity(f.path, cfg) for f in fs)
    for a in areas:
        fs = groups[a]
        eff = sum(f.effective for f in fs)
        if eff >= UNIT_TARGET * 0.5:
            packed.append((a, fs))
            continue
        k = (a.split("/")[0], is_sens(fs))
        names, cur = bins.get(k, ([], []))
        if cur and sum(f.effective for f in cur) + eff > UNIT_TARGET:
            packed.append((" + ".join(names), cur))
            names, cur = [], []
        bins[k] = (names + [a], cur + fs)
    for names, cur in bins.values():
        if cur:
            packed.append((" + ".join(names) if len(names) <= 4 else f"{names[0]} + {len(names) - 1} more areas", cur))
    packed.sort(key=lambda x: x[0])
    units = [mk(f"u{i + 1:02d}", name, fs) for i, (name, fs) in enumerate(packed)]

    # tests and docs go with the unit that shares the longest directory prefix (else none)
    by_uid = {u.uid: u for u in units}
    code_files = {u.uid: list(u.files) for u in units}
    for r in reviewable:
        if r.kind == "test":
            u = best_unit_from(r.path, units, code_files)
            if u is None:
                u = by_uid.setdefault("u00", Unit(uid="u00", name="tests without code in this PR", files=[],
                                                   sensitive=False, sensitive_hits=[], effective=0.0))
            u.files.append(r.path)
            u.test_files += 1
            u.effective += r.effective
    for r in recs:
        if r.kind == "docs":
            u = best_unit_from(r.path, units, code_files)
            if u is not None:
                u.docs_files.append(r.path)
    units = sorted(by_uid.values(), key=lambda u: u.uid)
    for u in units:
        for p in u.files:
            for r in recs:
                if r.path == p:
                    r.unit = u.uid
    return units


def _common_dir(a: str, b: str) -> str:
    pa, pb = PurePosixPath(a).parts[:-1], PurePosixPath(b).parts[:-1]
    n = 0
    while n < min(len(pa), len(pb)) and pa[n] == pb[n]:
        n += 1
    return "/".join(pa[:n])


def best_unit_from(path: str, units: list[Unit], code_files: dict[str, list[str]]) -> Unit | None:
    best, blen = None, 0
    for u in units:
        for fp in code_files.get(u.uid, []):
            cp = _common_dir(path, fp)
            ln = len(cp.split("/")) if cp else 0
            if ln > blen or (ln == blen and ln > 0 and best is not None and u.effective > best.effective):
                best, blen = u, ln
    return best


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
           "docs_diff", "changed_symbols", "plan_units", "filtered_table", "sizes", "size_class", "glob_match",
           "ATTRS", "NORMAL_MAX", "LARGE_MAX", "TEST_WEIGHT", "DIFF_CAP"]
