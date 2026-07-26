# Plan (à reprendre plus tard) : `create_scrape_session` pilotable à distance, sans exposer le CDP

**Statut : proposé, non démarré.** Discuté le 2026-07-26, mis en attente à la demande de l'utilisateur.

## Problème

`create_scrape_session` (`linkedin_mcp/server.py:590`) ouvre aujourd'hui un `BrowserManager(headless=False)` **local** et attend un login manuel dans une fenêtre Chromium visible sur la machine qui exécute le serveur. Sur le pod k3s `linkedin-mcp-prod` (headless, sans écran, sans Chromium local — voir [ADR-001](../adr/001-k3s-litellm-mcp-gateway.md)), ce tool est inutilisable. Aujourd'hui, le renouvellement de session (`linkedin_session.json`, cf. [ADR-005 linkedin_scraper](https://github.com/vinzlac/linkedin_scraper/blob/master/docs/adr/005-playwright-storage-state.md)) nécessite de :
1. Lancer `create_scrape_session` en local sur le Mac (via le serveur `linkedin-mcp` local dans Claude Desktop, ou `uv run python create_session.py`)
2. Resceller les secrets (`./scripts/seal-secrets.sh`)
3. Committer/pousser le `.sealed.yaml`, laisser ArgoCD synchroniser
4. Redémarrer le pod

Fréquence réelle : faible (session valable plusieurs semaines à mois selon activité), donc pas urgent — mais le cycle actuel est long et nécessite le Mac.

## Option écartée : exposer le CDP du Chromium partagé (Tailscale ou autre)

Idée initiale : faire pointer `create_scrape_session` vers le Chromium CDP partagé (`chromium-cdp-pod.openclaw`, IP `10.43.79.244:9222` — voir [post-mortem 2026-07-26](../post-mortem/2026-07-26-k3s-litellm-mcp-gateway.md) sur la contrainte Host-header IP-only de Chromium), et exposer son inspecteur DevTools (`http://<ip>:9222`) pour permettre l'étape humaine (login/2FA) — via Tailscale pour rester privé.

**Écartée** à la demande de l'utilisateur : il souhaite sécuriser l'accès avec la clé API LiteLLM déjà en place plutôt qu'ajouter Tailscale. Or la clé LiteLLM protège les *appels aux tools MCP* via le gateway — elle ne protège pas le port CDP brut (Chromium n'a **aucune authentification native** sur son protocole de debug distant ; quiconque atteint le port a un contrôle total du navigateur : lecture cookies, exécution JS arbitraire, exfiltration de la session). Exposer le CDP nécessiterait de construire soi-même une couche d'auth (reverse-proxy) — ce que LiteLLM ne fait pas pour un port qui n'est pas le sien.

## Option retenue (à implémenter) : cycle screenshot + action, entièrement via tools MCP

Ne jamais exposer le CDP directement. Piloter l'étape humaine (login/2FA) **via de nouveaux tools MCP**, protégés par le même mécanisme d'auth déjà validé (team + clé scopée LiteLLM, `mcp_tool_permissions`) — sans nouvelle surface réseau.

### 1. Nouveaux tools côté `linkedin_mcp/server.py`

| Tool | Rôle |
|---|---|
| `create_scrape_session_start` | Ouvre `linkedin.com/login` sur le Chromium CDP partagé (`BrowserManager(headless=False, cdp_url=settings.LINKEDIN_CDP_URL)` — actuellement `create_scrape_session` n'utilise **pas** `cdp_url`, contrairement à `_get_browser()`), prend une capture d'écran (`page.screenshot()`), la retourne en image dans le contenu MCP |
| `create_scrape_session_click(x, y)` | Clique aux coordonnées données, retourne une nouvelle capture |
| `create_scrape_session_type(text)` | Saisit du texte (email, mot de passe, code 2FA), retourne une nouvelle capture |
| `create_scrape_session_key(key)` | Envoie une touche spéciale (Enter, Tab), retourne une nouvelle capture |
| `create_scrape_session_finish` | Détecte l'arrivée sur le feed (même logique que `wait_for_manual_login` actuel, appelée en polling plutôt qu'en attente bloquante), sauvegarde la session via `browser.save_session()`, ferme le navigateur |

Utilisation : depuis Claude Desktop (`linkedin-mcp-prod`), Claude affiche les captures, l'utilisateur dicte les actions (clic/saisie), jusqu'à connexion réussie.

### 2. Volume persistant pour la session

Remplacer (ou compléter) le montage secret read-only actuel de `LINKEDIN_SESSION_PATH` par un `PersistentVolumeClaim` monté en écriture dans `kubernetes/deployment.yaml`, pour que `create_scrape_session_finish` puisse écrire directement la session sans passer par `seal-secrets.sh` + commit + resync ArgoCD.

**Compromis de sécurité à trancher avant implémentation** : un `SealedSecret` est chiffré au repos dans git ; un PVC ne l'est pas nativement (dépend du chiffrement du storage backend du cluster homelab). Le fichier de session (équivalent d'un mot de passe, cf. ADR-005) serait protégé uniquement par les permissions k8s (RBAC + accès au node) plutôt que par le chiffrement SealedSecret — acceptable ou non selon le niveau de confiance dans l'isolation du homelab.

### Ce qui reste hors scope de ce plan

- `authenticate` (OAuth officiel LinkedIn) — nécessite un callback HTTP local sur la machine qui initie le flow, pas transposable de la même façon à un pod distant. Reste un flow local uniquement.
- Les credentials LinkedIn (email/mot de passe) ne sont jamais stockés en clair, ni avant ni après ce changement — seule la façon dont la session Playwright est *établie* évolue.

### Effort estimé

- Nouveaux tools (`server.py`) : le plus gros morceau — logique de screenshot/action/détection de fin, gestion des erreurs (mauvais mot de passe, CAPTCHA imprévu, timeout).
- `cdp_url` sur `create_scrape_session*` : petit changement (~10 lignes), calqué sur `_get_browser()`.
- PVC : modification `deployment.yaml` + décision de StorageClass (taille ~quelques Mo suffit).

## Références

- [ADR-001 linkedin-mcp — Déploiement k3s exposé via le MCP Gateway LiteLLM](../adr/001-k3s-litellm-mcp-gateway.md)
- [Post-mortem 2026-07-26 — déploiement k3s + MCP Gateway](../post-mortem/2026-07-26-k3s-litellm-mcp-gateway.md)
- [ADR-005 linkedin_scraper — Playwright storage state](https://github.com/vinzlac/linkedin_scraper/blob/master/docs/adr/005-playwright-storage-state.md)
- Code actuel : `linkedin_mcp/server.py:590` (`create_scrape_session`), `linkedin_mcp/server.py:101` (`_get_browser`, seul endroit utilisant déjà `cdp_url`)
