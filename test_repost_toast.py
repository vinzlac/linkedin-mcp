#!/usr/bin/env python3
"""Vérifie la classification des toasts repost — sans navigateur.

Contexte : le 2026-09-07, le même post a été reposté deux fois à 4 minutes
d'intervalle. LinkedIn a refusé le doublon avec un toast explicite, que
`_verify_repost_published` lisait, journalisait, puis ignorait faute de
correspondre à sa regex de succès — pour conclure au succès par défaut juste
après. Le refus était donc rapporté comme une publication.

Cette logique est pure : elle se vérifie sans session ni navigateur, contrairement
aux autres scripts `test_*.py` de ce dépôt.

Usage:
    uv run python test_repost_toast.py
"""
import sys

from linkedin_mcp.linkedin.repost_ui import classify_repost_toast

# (texte du toast, issue attendue, pourquoi ce cas existe)
CASES = [
    # Texte réellement observé dans les logs du 2026-09-07.
    ("Ce post a déjà été créé.", "already", "refus de doublon observé en production"),
    ("Ce post a déjà été publié", "already", "variante de formulation"),
    ("You have already reposted this post", "already", "interface en anglais"),
    ("Post republié", "published", "succès nominal"),
    ("Repost successful", "published", "succès en anglais"),
    ("Une erreur est survenue, veuillez réessayer", "failed", "échec explicite"),
    ("Bienvenue sur LinkedIn", "unknown", "toast sans rapport : ne rien conclure"),
]


def main() -> int:
    failures = []
    for text, expected, why in CASES:
        got = classify_repost_toast(text)
        status = "ok " if got == expected else "KO "
        print(f"{status} {text!r} → {got} (attendu {expected}) — {why}")
        if got != expected:
            failures.append((text, expected, got))

    # Le refus contient « créé » : un motif de succès trop large le classerait
    # en publication. C'est exactement ce qui s'est produit.
    assert classify_repost_toast("Ce post a déjà été créé.") != "published"

    if failures:
        print(f"\n{len(failures)} cas en échec", file=sys.stderr)
        return 1
    print(f"\n{len(CASES)} cas vérifiés")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
