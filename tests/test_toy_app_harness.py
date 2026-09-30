"""Regression test for a real bug: lab/repos/toy-app/package.json's
"test" script used `node --test tests/` (a bare directory argument),
which fails with `Cannot find module '.../tests'` under Node 22/25 --
confirmed to reproduce identically with plain `node`, no Docker, no
mounts, no IDHN involved at all. Not a filesystem-enforcement bug: it's
the toy repo's own test-runner invocation, fixed the same way
benchmark/corpus/js-repo-2's package.json already does it (an explicit
`tests/*.test.js` glob). This guards against silently reverting to the
broken form; the real end-to-end confirmation is an actual `idhn run`.
"""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_toy_app_test_script_does_not_use_bare_test_directory():
    package_json = json.loads((REPO_ROOT / "lab" / "repos" / "toy-app" / "package.json").read_text(encoding="utf-8"))
    test_script = package_json["scripts"]["test"]
    assert test_script != "node --test tests/", (
        "node --test with a bare directory argument fails with "
        "'Cannot find module .../tests' under Node 22/25 -- use an "
        "explicit glob (tests/*.test.js) instead"
    )
    assert "tests/*.test.js" in test_script
