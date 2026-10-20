# External feedback kit (Week 6, Thursday)

For showing IDHN to 2–3 developers or security engineers and
collecting genuine reactions. This is a working tool for you to use,
not itself a finding — the actual feedback you collect is what goes
into the research report and final assessment, and nothing here
should be mistaken for that feedback.

## Before the session (5 min prep)

Have these open/ready:
- `README.md`'s top section (what IDHN is, one paragraph).
- `docs/demo-security-boundary.md` (the walkthrough below follows it).
- A terminal in this repo, able to run `idhn m3-report demo-security-boundary --policy policies/examples/coding-safe.yaml` live if they want to see it themselves.

## The walkthrough (10–15 min)

**1. Set up the premise (1 min).** "This is a system that sits between
a coding agent and your repo. The agent asks to do a task; IDHN figures
out the minimal permissions that task actually needs, and a container
enforces exactly that — not advisory, the agent's writes outside that
scope physically fail."

**2. Show the manifest** (`policies/examples/coding-safe.yaml`). Point
at the `write` scope specifically — just `src/**` and `tests/**`, not
the whole repo.

**3. Walk through the one real run** (`docs/demo-security-boundary.md`).
Read the task text aloud, then show:
- The diff: the real bug, correctly fixed, in the one authorized file.
- The blocked attempt: `package.json: Read-only file system` — say
  plainly that this wasn't a refusal from the agent, it's the
  container's own filesystem saying no.
- The audit output (`m3-report`): point at "unique files written: 1"
  and "blocked mid-attempt: 2."

**4. Show one real bug this project found in itself** (`docs/
symlink-write-bypass-fix.md`'s opening section). This is often the
most convincing part for a security-minded reviewer: not "we built
this and it's safe," but "we broke it ourselves, here's exactly how,
here's the fix, here's the regression test." Mention OpenAI's own
Codex CLI had a bug in the same class (safe-command allowlist trusting
`git show` by name) if the reviewer wants external corroboration that
this is a real, recurring problem class (`docs/novelty-comparison.md`).

**5. Show the cost** (`benchmark/results/v0.2.1-overhead-summary.md`).
Task success parity (20/24 vs. 23/24), mean overhead (+14.2%). Let them
react to whether that overhead is acceptable for their own use case —
this is exactly the kind of judgment call you want their opinion on,
not something to argue them out of either direction on.

**6. Show the limitations, unprompted** (`docs/
security-boundary-and-limitations.md`). Specifically: a host-level
attacker is out of scope by design; provider-side tool calls
(`web_search`) needed a separate fix and two more (`browser_use`,
`computer_use`) are still open; filesystem/process denials are only
auditable via the agent's own trace, not an independent log the way
network is. Showing this *before* they ask is deliberate — it signals
the report isn't trying to oversell.

## Questions to ask (in this order)

1. **"Would you actually use this?"** Push past yes/no — for what kind
   of task, and what would stop you?
2. **"What do you distrust about it?"** Let them name the thing
   unprompted before suggesting candidates yourself. If they stall,
   prompt with: the agent's own self-reported trace being the only
   audit evidence for filesystem/process denials; the compiler
   under-predicting what a task needs (T19); the +14.2% overhead; the
   host-level-attacker boundary.
3. **"What would break if you tried to integrate this into your own
   workflow?"** CI pipeline fit, multi-repo setups, needing a different
   agent than Codex, Windows vs. Linux hosts, existing permission
   systems it would need to coexist with.
4. **"Does the manifest/capability model make sense, or is it more
   friction than you'd want to deal with per task?"** This is really
   asking whether the compiler's automation is doing enough work that
   a human reviewing `compiled-envelope.yaml` once per task is
   tolerable, or whether that step itself is the friction.
5. **"Compared to just running the agent in a plain Docker container
   yourself, what would make you pick this instead?"** Their own
   answer to the roadmap's central question, independent of yours
   (`docs/novelty-comparison.md`'s own answer) — worth seeing if they
   converge or diverge.
6. **"What's missing from the audit trail or the limitations doc that
   you'd want before trusting this in production?"**

## After the session

Write down, verbatim where possible:
- Would-they-use-it, and under what condition.
- The distrust they named first, unprompted.
- Any integration problem that's specific to their own stack (not
  generic).
- Anything that surprised you — a reaction you didn't expect, positive
  or negative.

Three of these, collected honestly, are worth more in the final report
than a larger number collected superficially. If a reviewer pushes back
hard on something, that's the most valuable data point of the session,
not something to talk them out of.
