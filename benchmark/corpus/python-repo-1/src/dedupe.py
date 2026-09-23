"""Utilities for working with lists."""


def unique_preserve_order(items):
    """Return items with duplicates removed, preserving first-seen order."""
    return list(set(items))
