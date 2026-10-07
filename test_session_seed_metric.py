#!/usr/bin/env python3
"""Tests unitaires (sans navigateur) : comptage des amorçages de session.

Usage:
    uv run python test_session_seed_metric.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from linkedin_mcp.metrics import SESSION_SEEDS_TOTAL, record_session_seed

failures = 0


def check(label: str, cond: bool) -> None:
    global failures
    print(("OK  " if cond else "FAIL") + " " + label)
    if not cond:
        failures += 1


before = SESSION_SEEDS_TOTAL._value.get()
record_session_seed(False)
check("une session conservée ne compte pas", SESSION_SEEDS_TOTAL._value.get() == before)
record_session_seed(True)
check("un amorçage compte une fois", SESSION_SEEDS_TOTAL._value.get() == before + 1)

sys.exit(1 if failures else 0)
