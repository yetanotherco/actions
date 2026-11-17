"""plan_units puts every code and test file of the PR in exactly one unit.

Offline: no git (package_roots is replaced), no network, fixed random seed.
Run: python3 -I -B -m unittest discover -s ai-review/tests -v
"""

from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import seed  # noqa: E402


def rec(path: str, add: int, kind: str = "code") -> seed.FileRec:
    return seed.FileRec(path=path, status="M", old_path=None, add=add, dele=0, add_raw=add, del_raw=0, kind=kind)


def plan(recs: list, roots=(), cfg: dict | None = None) -> list:
    with mock.patch.object(seed, "package_roots", lambda wt, head: sorted(roots, key=lambda s: (-s.count("/"), s))):
        return seed.plan_units(Path("/nonexistent"), "HEAD", recs, cfg or {})


class PlanUnits(unittest.TestCase):
    def assert_partition(self, recs: list, units: list) -> None:
        want = sorted(r.path for r in recs if r.kind in ("code", "test"))
        got = sorted(p for u in units for p in u.files)
        self.assertEqual(got, want, "every code/test file in exactly one unit")
        self.assertEqual(len({u.uid for u in units}), len(units), "unit ids are unique")
        for r in recs:
            if r.kind in ("code", "test"):
                self.assertTrue(r.unit, f"{r.path} has no unit")
        self.assertEqual(seed.not_reviewed(recs)["files"], [])

    def test_rust_module_tree_of_mod_rs(self):
        # #25: one oversize area chunked by files; chunks whose first and last basenames matched
        # ({mod.rs .. mod.rs}) replaced each other and only the last chunk was reviewed
        recs = [rec(f"prover/src/tables/air/cols/t{i:02d}/mod.rs", 900) for i in range(12)]
        self.assert_partition(recs, plan(recs, roots=["prover"]))

    def test_root_directory_named_root(self):
        # top-level files share the area "(root)" with a top-level directory named "(root)"
        recs = [rec("(root)/x.rs", 20), rec("x.rs", 2600), rec("y.rs", 2000)]
        self.assert_partition(recs, plan(recs))
        recs = [rec("(root)/z.rs", 3000), rec("z.rs", 3000)]
        self.assert_partition(recs, plan(recs))

    def test_a_file_in_no_unit_is_listed(self):
        r = rec("src/a.rs", 10)
        self.assertEqual(seed.not_reviewed([r])["files"], [{"file": "src/a.rs", "why": "not placed in a part"}])

    def test_random_layouts(self):
        rnd = random.Random(1)
        comps = ["crypto", "prover", "src", "x", "y", "tables", "mod", "a", "(root)"]
        for case in range(600):
            recs, seen = [], set()
            for _ in range(rnd.randint(1, 120)):
                p = "/".join([rnd.choice(comps) for _ in range(rnd.randint(1, 9) - 1)]
                             + [rnd.choice(["mod.rs", "lib.rs", "a.rs", "b.rs", "README.md"])])
                if p in seen:
                    continue
                seen.add(p)
                kind = "docs" if p.endswith(".md") else ("test" if rnd.random() < 0.15 else "code")
                recs.append(rec(p, rnd.choice([1, 10, 300, 1200, 2600, 5000, 9000]), kind))
            roots = rnd.sample(["crypto", "prover", "prover/src", "x/y", "src", "a/mod"], rnd.randint(0, 4))
            cfg = {"low_risk": rnd.sample(["prover/**", "crypto/**", "**/mod.rs"], rnd.randint(0, 2))}
            with self.subTest(case=case):
                self.assert_partition(recs, plan(recs, roots, cfg))

    def test_low_risk_areas_get_their_own_parts(self):
        # small areas of one top-level directory are packed together, but never low-risk with the rest
        cfg = {"low_risk": ["sdk/examples/**"]}
        recs = [rec("core/big.rs", 3000), rec("core/more.rs", 2500), rec("sdk/core/sign.rs", 300),
                rec("sdk/examples/demo.ts", 300), rec("sdk/examples/app.ts", 200), rec("sdk/tests/sign.rs", 100, "test")]
        units = plan(recs, cfg=cfg)
        self.assert_partition(recs, units)
        by_file = {p: u for u in units for p in u.files}
        self.assertTrue(by_file["sdk/examples/demo.ts"].low_risk)
        self.assertIs(by_file["sdk/examples/demo.ts"], by_file["sdk/examples/app.ts"])
        self.assertFalse(by_file["sdk/core/sign.rs"].low_risk)
        self.assertIsNot(by_file["sdk/core/sign.rs"], by_file["sdk/examples/demo.ts"])

    def test_a_part_is_low_risk_only_if_all_its_code_is(self):
        cfg = {"low_risk": ["examples/**"]}
        self.assertTrue(plan([rec("examples/a.rs", 50), rec("examples/tests/a.rs", 10, "test")], cfg=cfg)[0].low_risk)
        self.assertFalse(plan([rec("examples/a.rs", 50), rec("src/a.rs", 1)], cfg=cfg)[0].low_risk)
        self.assertFalse(plan([rec("examples/a.rs", 50)])[0].low_risk)   # no config: nothing is low-risk


if __name__ == "__main__":
    unittest.main(verbosity=2)
