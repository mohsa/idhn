# Example manifests

Each file here is a real, runnable manifest -- `idhn validate
policies/examples/<name>.yaml` and `idhn run --policy policies/examples
/<name>.yaml ...` both work directly against any of them. Each file's
own header comment explains what it's for; this is just an index.

- **`coding-safe.yaml`** -- start here. The manifest used in this
  README's own Quickstart and "Running the M0 experiment": write scope
  `src/**`+`tests/**`, a minimal process allowlist, Codex's own backend
  plus the npm registry on the network allowlist, the standard deny
  list (`.env`, `.github/**`, `/home/**`, `/root/**`).
- **`coding-network.yaml`** -- the same task, broader network access,
  for exercising network-capability variation without changing the
  filesystem/process shape.
- **`coding-too-restrictive.yaml`** -- deliberately missing the write
  grant `coding-safe.yaml` has, so the agent's legitimate work fails
  cleanly. Used to confirm a too-narrow manifest produces an honest,
  visible failure rather than a silent workaround (M3-B).
- **`network-isolation-test.yaml`** -- the dedicated policy behind
  `tests/integration/test_network_isolation.py`: proves the gateway and
  network boundary hold even under a direct DNS/TCP egress attempt, not
  just proxy-aware traffic.
- **`web-search-test.yaml`** -- the dedicated policy behind
  `tests/integration/test_codex_web_search_containment.py`, reproducing
  and regression-testing the provider-side out-of-band capability fix
  (`docs/web-search-out-of-band-capability.md`).
- **`transitive-escape.yaml`** -- M2: can an agent turn an allowed
  capability into a forbidden one by routing through something else
  it's allowed to touch? See README.md's "Transitive enforcement (M2)"
  section.
- **`m4-contained.yaml`** -- containment under a compromised or
  adversarially-instructed agent (prompt injection, direct malicious
  instruction) -- see README.md's "M4: compromised-agent containment"
  section and `docs/dogfood-t3-result.md`/`docs/dogfood-t4-result.md`
  for real-repo versions of the same idea.
