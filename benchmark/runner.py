"""Benchmark runner for IDHN task/attack scenarios. NOT implemented in v0.1.

This will eventually load a benchmark/tasks or benchmark/attacks YAML
file, run it through `idhn run`, and assert the expected outcome
(tests_pass / blocked) against the resulting report.json. Building this
is downstream of the M0-M2 milestones -- see the top-level README.
"""
from __future__ import annotations


def main() -> None:
    raise NotImplementedError("benchmark runner is not implemented in IDHN v0.1")


if __name__ == "__main__":
    main()
