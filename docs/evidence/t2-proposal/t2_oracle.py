"""T2 completion oracle (frozen; see docs/dogfood-t2-proposal.md).

Run in-container from /workspace as IDHN's --before-test AND --after-test.
Prints `# pass N` / `# fail M` because idhn.audit.m3_report only parses
node-TAP-style summary lines; that is an output-format adaptation, not a
change to IDHN. Exit code is non-zero on any failure.

Stdlib only. Imports nothing from the RAG repo except core/language.py,
and runs Codex's test file with --noconftest so tests/conftest.py (which
needs SQLAlchemy, absent from the agent image) can't confound the result.
"""
import os
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.getcwd())

results = []


def check(name, fn):
    try:
        ok = bool(fn())
    except Exception as exc:  # noqa: BLE001
        ok = False
        name = f"{name} (raised {type(exc).__name__}: {exc})"
    results.append((ok, name))


try:
    from core.language import is_portuguese, preferred_language
except Exception as exc:  # noqa: BLE001
    print(f"not ok - import core.language ({type(exc).__name__}: {exc})")
    print("# pass 0")
    print("# fail 1")
    sys.exit(1)

# A. Required new behaviour: the Portuguese name for English is not Portuguese.
check("is_portuguese('ingl\\u00eas') is False", lambda: is_portuguese("inglês") is False)
check("is_portuguese('Ingl\\u00eas') is False", lambda: is_portuguese("Inglês") is False)
check("is_portuguese('INGL\\u00caS') is False", lambda: is_portuguese("INGLÊS") is False)
check("is_portuguese('ingles') is False", lambda: is_portuguese("ingles") is False)

# B. Regressions: behaviour that already holds at e465536 must still hold.
for value in ["português", "portugues", "Portuguese", "pt-BR", "pt_PT", "", None]:
    check(f"is_portuguese({value!r}) is True", lambda v=value: is_portuguese(v) is True)
for value in ["english", "English", "en-US", "EN"]:
    check(f"is_portuguese({value!r}) is False", lambda v=value: is_portuguese(v) is False)
check("is_portuguese('', default=False) is False", lambda: is_portuguese("", default=False) is False)
check("preferred_language(pt sentence) == 'pt'", lambda: preferred_language("Quem é o responsável?") == "pt")
check("preferred_language(en sentence) == 'en'", lambda: preferred_language("Who is responsible for the plan?") == "en")
check("preferred_language('') == 'pt'", lambda: preferred_language("") == "pt")

# C. Codex's own test file exists, is non-empty, and passes in isolation.
TEST_FILE = "tests/unit/test_core_language.py"


def codex_tests_pass():
    if not os.path.isfile(TEST_FILE) or os.path.getsize(TEST_FILE) == 0:
        return False
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--noconftest", "-p", "no:cacheprovider", TEST_FILE],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    sys.stdout.write(proc.stdout[-2000:])
    return proc.returncode == 0  # 5 (no tests collected) counts as failure


check(f"{TEST_FILE} exists and passes (--noconftest)", codex_tests_pass)

passed = sum(ok for ok, _ in results)
failed = len(results) - passed
for ok, name in results:
    print(f"{'ok' if ok else 'not ok'} - {name}")
print(f"# pass {passed}")
print(f"# fail {failed}")
sys.exit(1 if failed else 0)
