"""Task -> capabilities compiler. NOT implemented in v0.1.

v0.1 requires an explicit, hand-written manifest per task. Automatically
deriving the minimal capability set for a given task description is
deferred until after the M0/M1/M2 milestones prove the enforcement
boundary itself works -- see the top-level README.
"""
from __future__ import annotations


def compile_task(task_description: str):
    raise NotImplementedError("task -> capability compilation is not implemented in IDHN v0.1")
