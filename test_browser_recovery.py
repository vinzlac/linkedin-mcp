#!/usr/bin/env python3
"""Tests unitaires (sans navigateur) pour la récupération après crash Playwright
et la classification des erreurs OAuth du repost.

Usage:
    uv run python test_browser_recovery.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from linkedin_mcp.linkedin.browser_recovery import is_recoverable_browser_error
from linkedin_mcp.linkedin.auth import AuthError, TokenExpiredError
from linkedin_mcp.linkedin.repost import RepostError, RepostForbiddenError, is_repost_api_forbidden


PASSED = 0
FAILED = 0


def check(label: str, cond: bool) -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✅ {label}")
    else:
        FAILED += 1
        print(f"  ❌ {label}")


print("is_recoverable_browser_error — vrai sur les crashs navigateur/page")
for msg in (
    "Page.goto: Page crashed",
    "Page.evaluate: Execution context was destroyed, most likely because of a navigation",
    "Page.goto: Target page, context or browser has been closed",
    "Target closed",
    "Browser has been closed",
    "browserContext.newPage: Connection closed",
):
    check(repr(msg[:45]), is_recoverable_browser_error(RuntimeError(msg)))

print("is_recoverable_browser_error — faux sur les erreurs applicatives")
for msg in (
    "Bouton Republier introuvable sur la page post",
    "Feed LinkedIn non chargé (session expirée ?)",
    "User info request failed with status: 401",
    "Timeout 45000ms exceeded",
):
    check(repr(msg[:45]), not is_recoverable_browser_error(RuntimeError(msg)))

print("TokenExpiredError est une AuthError (fallback Playwright dans repost_post)")
check("issubclass", issubclass(TokenExpiredError, AuthError))
check("isinstance", isinstance(TokenExpiredError("x"), AuthError))

print("Classification API repost")
check("403 -> forbidden", is_repost_api_forbidden(RepostForbiddenError("Accès refusé (403)")))
check("400 -> pas fallback", not is_repost_api_forbidden(RepostError("400: bad request")))
check(
    "401 API repost -> TokenExpiredError (AuthError, fallback géré par repost_post)",
    issubclass(TokenExpiredError, AuthError),
)

print()
print(f"{PASSED} passés, {FAILED} échoués")
sys.exit(1 if FAILED else 0)
