#!/usr/bin/env python3
"""Crée le fichier de session LinkedIn (linkedin_session.json) pour le scraping.

Ce script ouvre un navigateur Chromium pour un login manuel. Une fois connecté,
la session est sauvegardée dans linkedin_session.json à la racine du projet.

Usage:
    uv run python create_session.py

Équivalent depuis Claude Desktop : outil MCP create_scrape_session.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from linkedin_scraper import BrowserManager, wait_for_manual_login

SESSION_PATH = Path(__file__).parent / "linkedin_session.json"


async def main() -> None:
    print("=" * 60)
    print("Création de la session LinkedIn")
    print("=" * 60)
    print("\nÉtapes :")
    print("1. Un navigateur va s'ouvrir sur la page de login LinkedIn")
    print("2. Connecte-toi manuellement (email, mot de passe, 2FA...)")
    print("3. Attends que ton feed LinkedIn soit affiché")
    print("4. La session sera sauvegardée automatiquement")
    print("\n" + "=" * 60 + "\n")

    async with BrowserManager(headless=False) as browser:
        print("Ouverture de la page de login LinkedIn...")
        await browser.page.goto("https://www.linkedin.com/login")

        print("\n🔐 Connecte-toi dans la fenêtre du navigateur...")
        print("   (5 minutes maximum)")
        print("\n⏳ En attente...\n")

        try:
            await wait_for_manual_login(browser.page, timeout=300_000)
        except Exception as e:
            print(f"\n❌ Échec du login : {e}")
            print("   Réessaie et attends que le feed LinkedIn soit chargé.")
            sys.exit(1)

        print(f"\n💾 Sauvegarde de la session dans {SESSION_PATH}...")
        await browser.save_session(str(SESSION_PATH))

    print("\n" + "=" * 60)
    print("✅ Session créée avec succès.")
    print("=" * 60)
    print(f"\nFichier : {SESSION_PATH}")
    print("\nTu peux maintenant lancer :")
    print("  uv run python test_scrape_feed.py   # JSON dans output/feed.json")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
