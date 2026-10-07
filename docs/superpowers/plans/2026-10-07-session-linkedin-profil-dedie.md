# Session LinkedIn stable — profil Chromium dédié, amorçage unique — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal :** la session LinkedIn de `linkedin-mcp` survit aux redémarrages du pod et du navigateur, et n'est plus jamais écrasée par la copie figée du Sealed Secret.

**Architecture :** `linkedin-mcp` pilote par CDP un **Chromium dédié** sur `gpu-node` (nouvelle instance `chromium-cdp-linkedin`, profil persistant non partagé). Le profil de ce navigateur devient la **source de vérité** de la session. Le fichier de session (Sealed Secret) ne sert plus qu'à **amorcer** un profil qui n'a pas de cookie `li_at`, après un effacement préalable des cookies LinkedIn. La session se crée et se renouvelle **dans ce Chromium**, via DevTools à travers un tunnel SSH, et non plus sur le Mac.

**Tech Stack :** Python 3.12, Playwright (`connect_over_cdp`), `linkedin-playwright-scraper` (dépôt `~/workspace/linkedin_scraper`, PyPI), FastMCP, prometheus_client, Ansible (`k3s-homelab`), Kubernetes et Argo CD, promtool.

**Spec :** pas de spec séparée. Le constat et les décisions sont dans la section « Constat et décisions » ci-dessous. Enquête du 2026-10-07 : post-mortem 045 de `k3s-homelab` et session de diagnostic.

---

## Constat et décisions

**Constat (2026-10-07)**

1. En k3s, `linkedin-mcp` pilote par CDP le Chromium de `gpu-node` `chromium-cdp` (ports 9222/9223), avec le profil `/data/volumes/openclaw-browser/profile`, **partagé avec OpenClaw**. Ce Chromium redémarre chaque jour à 06:00 (redémarrage préventif) et à chaque reboot du nœud.
2. Avec `LINKEDIN_PERSISTENT_CONTEXT=true` (par défaut), `BrowserManager.load_session()` prend `contexts[0]` puis fait `add_cookies(storage_state_cookies(fichier))` **sans rien effacer** (`linkedin_scraper/core/browser.py:393-401`).
3. `load_session()` est appelé à **chaque création du navigateur** : démarrage du pod, sonde de vie en échec, réessai après crash (`linkedin_mcp/server.py:457-519`). Les cookies figés du secret écrasent donc ceux que LinkedIn a rafraîchis dans le profil.
4. La session n'est jamais réécrite : le fichier monté est en lecture seule, et `set_scrape_session_json` échoue en k3s.
5. Une session neuve créée sur le Mac (15:59), testée sur le Mac (16:00) puis injectée (16:02) a perdu son cookie `li_at` **côté serveur** avant 16:08. Les autres cookies sont restés, et OpenClaw était inactif sur la période. Hypothèse retenue : le même jeton a été vu depuis deux empreintes de navigateur en quelques minutes.
6. Le mode contexte isolé (`new_context`) est exclu : chaque contexte neuf est un appareil inconnu, ce qui déclenche une vérification de sécurité à chaque scrape (incident du 2026-09-04, docstring de `LINKEDIN_PERSISTENT_CONTEXT`).

**Décisions**

| # | Décision |
|---|---|
| D1 | Instance Chromium **dédiée** `chromium-cdp-linkedin` sur `gpu-node` : port exposé **9242**, port interne **9243**, profil `/data/volumes/linkedin-browser`. Même playbook que `chromium-cdp-betclic-info` |
| D2 | **Pas de redémarrage préventif** de cette instance. `linkedin-mcp` garde un seul onglet, garé sur `about:blank`, plus l'onglet de la feed session. La sonde de vie et `MemoryMax` couvrent les pannes |
| D3 | En mode persistant, `load_session()` **n'injecte le fichier que si le profil n'a pas de `li_at` valide**. Avant d'injecter, il efface les cookies LinkedIn du profil (`clear_cookies(domain=…linkedin.com)`). Paramètre `force=True` pour un réamorçage volontaire |
| D4 | `BrowserManager.session_seeded` (bool) indique si le dernier `load_session()` a injecté. `linkedin-mcp` journalise en WARNING et incrémente `linkedin_mcp_session_seeds_total`. L'alerte `LinkedinSessionSeeded` se déclenche quand le compteur bouge : LinkedIn a supprimé la session, ou le profil a été remis à zéro |
| D5 | **Création et renouvellement de la session dans le Chromium dédié**, via DevTools à travers un tunnel SSH. Le Sealed Secret reste un amorçage de secours pour un profil vierge. On ne réutilise plus une session sur le Mac après l'avoir créée |
| D6 | `linkedin-playwright-scraper` **4.6.0**, avec `playwright>=1.43` (filtres de `clear_cookies`). `linkedin-mcp` passe à `>=4.6.0` |

## Global Constraints

- **Aucune action sur LinkedIn pendant le développement** des tâches 1 à 3 : tests unitaires uniquement, avec des faux objets. Aucun scrape réel avant la tâche 6.
- **Publication PyPI** (tâche 2) : **demander confirmation à l'utilisateur** avant `twine upload`, car c'est irréversible et public.
- **Push et déploiement** (tâches 4 à 6) : un commit par tâche ; ne rien pousser vers `k3s-homelab` ni `linkedin-mcp` sans le feu vert de l'utilisateur.
- **Ne pas modifier** l'instance partagée `chromium-cdp` (OpenClaw), ni son profil.
- Langue des commentaires, logs et docs : **français**, comme dans le code existant.
- Ports : **9242/9243** pour la nouvelle instance (vérifier qu'ils sont libres sur `gpu-node`, tâche 4).
- URL CDP dans le pod : **IP**, jamais un nom DNS. Chrome rejette un en-tête `Host` qui n'est ni une IP ni localhost (commentaire de `kubernetes/deployment.yaml:63-67`).

## Review Focus

1. **Profil qui a un `li_at` expiré** (date `expires` passée) : il faut réamorcer. Test `test_seeds_when_profile_session_cookie_is_expired` (tâche 1).
2. **Cookie de session sans date** (`expires == -1`, cookie de session navigateur) : il compte comme présent, donc pas d'injection. Test `test_session_cookie_without_expiry_counts_as_present` (tâche 1).
3. **Cookies d'autres sites dans le profil** : `clear_cookies` ne doit toucher qu'aux domaines `linkedin.com`. Test `test_clear_targets_only_linkedin_domains` (tâche 1).
4. **Deuxième `load_session()` dans le même processus** (relance après sonde en échec) : aucune réinjection si le premier a amorcé. Test `test_second_load_does_not_reseed` (tâche 1).
5. **Redémarrage du pod puis du service Chromium** : la session tient sans réinjection. Vérification réelle en tâche 6, étapes 6 et 7 (compteur à 0, log « conservée »).

---

## Task 1 : amorçage unique dans `linkedin_scraper`

**Files :**
- Modify : `~/workspace/linkedin_scraper/linkedin_scraper/core/browser.py` (imports l.3-11, `__init__` l.152, `load_session` l.371-401)
- Test : `~/workspace/linkedin_scraper/tests/test_browser_session.py` (classes `_FakeCtx` l.79-96 et `TestPersistentContext`)

**Interfaces :**
- Produces :
  - `BrowserManager.load_session(self, filepath: str, force: bool = False) -> None` ;
  - attribut `BrowserManager.session_seeded: bool` (False tant qu'aucun `load_session` n'a injecté) ;
  - constante module `SESSION_COOKIE = "li_at"`.

- [ ] **Step 1 : étendre le faux contexte et écrire les tests qui échouent**

Dans `tests/test_browser_session.py`, remplacer la classe `_FakeCtx` par :

```python
class _FakeCtx:
    """Contexte Playwright minimal : un « bocal » de cookies au format Playwright."""

    def __init__(self, pages=None, jar=None):
        self.pages = pages or []
        self.added = []
        self.jar = list(jar or [])
        self.cleared_with = []
        self.closed = False
        self._new_pages = 0

    async def add_cookies(self, cookies):
        self.added.extend(cookies)
        self.jar.extend(cookies)

    async def cookies(self, urls=None):
        return list(self.jar)

    async def clear_cookies(self, name=None, domain=None, path=None):
        self.cleared_with.append(domain)
        self.jar = [c for c in self.jar if not domain.search(c["domain"])]

    async def new_page(self):
        self._new_pages += 1
        page = _FakePage()
        self.pages.append(page)
        return page

    async def close(self):
        self.closed = True
```

Puis ajouter, à la fin de `TestPersistentContext` (même indentation que les tests existants) :

```python
    @pytest.mark.asyncio
    async def test_profile_with_valid_session_is_left_alone(self, tmp_path):
        """Incident 2026-10-07 : à chaque relance, les cookies figés du secret
        écrasaient ceux que LinkedIn avait rafraîchis dans le profil."""
        live = {"name": "li_at", "value": "frais", "domain": ".www.linkedin.com",
                "path": "/", "expires": time.time() + 86400}
        existing = _FakeCtx(jar=[live])
        mgr = BrowserManager(cdp_url="http://x:9222", persistent_context=True)
        mgr._browser = _FakeBrowser([existing])

        await mgr.load_session(str(_session_file(tmp_path)))

        assert existing.added == [], "un profil déjà connecté ne doit rien recevoir"
        assert existing.cleared_with == []
        assert mgr.session_seeded is False

    @pytest.mark.asyncio
    async def test_seeds_and_clears_when_profile_has_no_session(self, tmp_path):
        stale = {"name": "JSESSIONID", "value": "vieux", "domain": ".www.linkedin.com", "path": "/"}
        existing = _FakeCtx(jar=[stale])
        mgr = BrowserManager(cdp_url="http://x:9222", persistent_context=True)
        mgr._browser = _FakeBrowser([existing])

        await mgr.load_session(str(_session_file(tmp_path)))

        assert mgr.session_seeded is True
        assert len(existing.cleared_with) == 1, "les cookies LinkedIn doivent être effacés avant"
        assert all(c["value"] != "vieux" for c in existing.jar), "aucun mélange ancien/nouveau"
        assert {c["name"] for c in existing.jar} == {"li_at", "_px3"}

    @pytest.mark.asyncio
    async def test_seeds_when_profile_session_cookie_is_expired(self, tmp_path):
        expired = {"name": "li_at", "value": "mort", "domain": ".www.linkedin.com",
                   "path": "/", "expires": time.time() - 60}
        existing = _FakeCtx(jar=[expired])
        mgr = BrowserManager(cdp_url="http://x:9222", persistent_context=True)
        mgr._browser = _FakeBrowser([existing])

        await mgr.load_session(str(_session_file(tmp_path)))

        assert mgr.session_seeded is True
        assert [c["value"] for c in existing.jar if c["name"] == "li_at"] == ["x"]

    @pytest.mark.asyncio
    async def test_session_cookie_without_expiry_counts_as_present(self, tmp_path):
        session_only = {"name": "li_at", "value": "frais", "domain": ".www.linkedin.com",
                        "path": "/", "expires": -1}
        existing = _FakeCtx(jar=[session_only])
        mgr = BrowserManager(cdp_url="http://x:9222", persistent_context=True)
        mgr._browser = _FakeBrowser([existing])

        await mgr.load_session(str(_session_file(tmp_path)))

        assert mgr.session_seeded is False
        assert existing.added == []

    @pytest.mark.asyncio
    async def test_force_reseeds_even_with_a_valid_session(self, tmp_path):
        live = {"name": "li_at", "value": "frais", "domain": ".www.linkedin.com",
                "path": "/", "expires": time.time() + 86400}
        existing = _FakeCtx(jar=[live])
        mgr = BrowserManager(cdp_url="http://x:9222", persistent_context=True)
        mgr._browser = _FakeBrowser([existing])

        await mgr.load_session(str(_session_file(tmp_path)), force=True)

        assert mgr.session_seeded is True
        assert [c["value"] for c in existing.jar if c["name"] == "li_at"] == ["x"]

    @pytest.mark.asyncio
    async def test_clear_targets_only_linkedin_domains(self, tmp_path):
        other = {"name": "sid", "value": "garde", "domain": ".example.com", "path": "/"}
        existing = _FakeCtx(jar=[other])
        mgr = BrowserManager(cdp_url="http://x:9222", persistent_context=True)
        mgr._browser = _FakeBrowser([existing])

        await mgr.load_session(str(_session_file(tmp_path)))

        pattern = existing.cleared_with[0]
        for domain in (".linkedin.com", ".www.linkedin.com", "www.linkedin.com"):
            assert pattern.search(domain), domain
        for domain in (".example.com", ".notlinkedin.com", "linkedin.com.evil.io"):
            assert not pattern.search(domain), domain
        assert other in existing.jar, "les cookies des autres sites ne doivent pas bouger"

    @pytest.mark.asyncio
    async def test_second_load_does_not_reseed(self, tmp_path):
        existing = _FakeCtx()
        mgr = BrowserManager(cdp_url="http://x:9222", persistent_context=True)
        mgr._browser = _FakeBrowser([existing])
        session = _session_file(tmp_path)

        await mgr.load_session(str(session))
        assert mgr.session_seeded is True
        injected = len(existing.added)

        await mgr.load_session(str(session))   # relance après sonde en échec

        assert mgr.session_seeded is False
        assert len(existing.added) == injected, "aucune réinjection au deuxième chargement"
```

Ajouter `import time` en tête du fichier de test, sous `import json`.

Le fichier de session de test (`_session_file`) crée un `li_at` **sans** champ `expires`. Le code doit donc traiter l'absence d'`expires` comme « sans date », c'est-à-dire présent. C'est ce qui rend `test_second_load_does_not_reseed` cohérent.

- [ ] **Step 2 : vérifier que les tests échouent**

Run : `cd ~/workspace/linkedin_scraper && uv run pytest tests/test_browser_session.py -k "PersistentContext" -v`
Expected : FAIL sur les 7 nouveaux tests (`AttributeError: 'BrowserManager' object has no attribute 'session_seeded'` ou `TypeError: load_session() got an unexpected keyword argument 'force'`). Les 4 tests existants de la classe passent.

- [ ] **Step 3 : implémentation minimale**

Dans `linkedin_scraper/core/browser.py` :

1. Imports (après `import os`) :

```python
import re
import time
```

2. Constantes, juste après les imports existants et avant `_UNSUPPORTED_COOKIE_FIELDS` (ou en tête du module si cette constante n'est pas en tête) :

```python
SESSION_COOKIE = "li_at"
"""Cookie qui porte l'authentification LinkedIn : sa présence fait foi."""

_LINKEDIN_COOKIE_DOMAIN = re.compile(r"(^|\.)linkedin\.com$")
_LINKEDIN_URL = "https://www.linkedin.com"
```

3. Dans `__init__`, après `self._is_authenticated = False` (l.152) :

```python
        # True si le dernier load_session() a injecté le fichier de session dans
        # le profil persistant (profil sans session, ou force=True).
        self.session_seeded = False
```

4. Nouvelle méthode, juste avant `async def load_session` :

```python
    async def _profile_has_session(self) -> bool:
        """Le profil persistant porte-t-il un `li_at` encore valide ?

        Un cookie sans date (`expires` absent ou -1) est un cookie de session du
        navigateur : il compte comme présent.
        """
        now = time.time()
        for cookie in await self._context.cookies(_LINKEDIN_URL):
            if cookie.get("name") != SESSION_COOKIE:
                continue
            expires = cookie.get("expires", -1)
            if expires in (-1, None) or expires > now:
                return True
        return False
```

5. Signature et docstring de `load_session` :

```python
    async def load_session(self, filepath: str, force: bool = False) -> None:
        """
        Load browser session from file.

        En mode persistant (navigateur distant par CDP), le profil du navigateur
        est la source de vérité : le fichier n'y est injecté que si le profil n'a
        pas de `li_at` valide, ou si ``force`` est vrai. Les cookies LinkedIn du
        profil sont alors effacés avant l'injection, pour ne jamais mélanger deux
        sessions (incident 2026-10-07).

        Args:
            filepath: Path to session file
            force: réinjecter même si le profil a déjà une session (mode persistant)
        """
```

6. Remplacer le bloc persistant (l.393-401) :

```python
        if self._use_persistent_context():
            self._context = self._browser.contexts[0]
            self._owns_context = False
            if force or not await self._profile_has_session():
                await self._context.clear_cookies(domain=_LINKEDIN_COOKIE_DOMAIN)
                await self._context.add_cookies(storage_state_cookies(filepath))
                self.session_seeded = True
                logger.warning(
                    "Profil sans session LinkedIn valide : session amorcée depuis %s",
                    filepath,
                )
            else:
                self.session_seeded = False
                logger.info(
                    "Session LinkedIn du profil conservée (%s non réinjecté)", filepath
                )
            await self._adopt_page_in_persistent_context()
            self._is_authenticated = True
            return
```

- [ ] **Step 4 : vérifier que tout passe**

Run : `cd ~/workspace/linkedin_scraper && uv run pytest tests/test_browser_session.py tests/test_browser.py -v`
Expected : PASS sur l'ensemble, nouveaux tests et tests existants (`test_reuses_the_browsers_own_context` amorce un profil vide, donc `added` contient bien `li_at` et `_px3`).

- [ ] **Step 5 : commit**

```bash
cd ~/workspace/linkedin_scraper
git add linkedin_scraper/core/browser.py tests/test_browser_session.py
git commit -m "fix(browser): n'amorcer la session que si le profil persistant n'en a pas

Les cookies figés du fichier de session écrasaient, à chaque relance du
navigateur, ceux que LinkedIn avait rafraîchis dans le profil CDP
(incident 2026-10-07). Injection seulement sans li_at valide, après
effacement des cookies LinkedIn ; force=True pour un réamorçage voulu ;
session_seeded pour le signaler.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

## Task 2 : publier `linkedin-playwright-scraper` 4.6.0

**Files :**
- Modify : `~/workspace/linkedin_scraper/pyproject.toml` (l.7 `version`, l.31 `playwright`)

**Interfaces :**
- Consumes : tâche 1.
- Produces : paquet PyPI `linkedin-playwright-scraper==4.6.0`.

- [ ] **Step 1 : version et dépendance**

Dans `pyproject.toml` : `version = "4.6.0"` et `"playwright>=1.43.0",` (les filtres de `clear_cookies` arrivent en 1.43).

- [ ] **Step 2 : suite complète et build**

Run : `cd ~/workspace/linkedin_scraper && uv lock && uv run pytest -m "not integration" && uv build`
Expected : tests PASS, puis `dist/linkedin_playwright_scraper-4.6.0-py3-none-any.whl` et `.tar.gz` créés. Si le marqueur `integration` n'existe pas, lancer `uv run pytest tests/ -q` et vérifier qu'aucun test n'ouvre de vrai navigateur (ceux qui exigent une session sont ignorés sans fichier de session).

- [ ] **Step 3 : commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(release): 4.6.0 — amorçage unique de la session en mode persistant

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 4 : publier, APRÈS confirmation explicite de l'utilisateur**

Demander : « Je publie linkedin-playwright-scraper 4.6.0 sur PyPI ? »
Puis : `just publish` (`uv run twine upload dist/*`), et `git tag v4.6.0 && git push origin main --tags`.
Expected : `https://pypi.org/project/linkedin-playwright-scraper/4.6.0/` répond 200.

## Task 3 : `linkedin-mcp` — dépendance, métrique, alerte

**Files :**
- Modify : `~/workspace/linkedin-mcp/pyproject.toml` (l.17), `uv.lock`
- Modify : `~/workspace/linkedin-mcp/linkedin_mcp/metrics.py` (après `TOOL_CALLS_TOTAL`)
- Modify : `~/workspace/linkedin-mcp/linkedin_mcp/server.py:488-495`
- Modify : `~/workspace/linkedin-mcp/kubernetes/prometheusrule.yaml`, `prometheusrule.rules.yaml`, `prometheusrule.test.yaml`
- Create : `~/workspace/linkedin-mcp/test_session_seed_metric.py`

**Interfaces :**
- Consumes : `BrowserManager.session_seeded` (tâche 1), PyPI 4.6.0 (tâche 2).
- Produces :
  - métrique `linkedin_mcp_session_seeds_total` (Counter, sans label) ;
  - fonction `record_session_seed(seeded: bool) -> None` dans `linkedin_mcp/metrics.py` ;
  - alerte `LinkedinSessionSeeded`.

- [ ] **Step 1 : test qui échoue** (style des tests racine du dépôt : script `check()`)

Créer `test_session_seed_metric.py` :

```python
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
```

Run : `cd ~/workspace/linkedin-mcp && uv run python test_session_seed_metric.py`
Expected : `ImportError: cannot import name 'SESSION_SEEDS_TOTAL'`.

- [ ] **Step 2 : métrique**

Dans `linkedin_mcp/metrics.py`, après la définition de `TOOL_CALLS_TOTAL` :

```python
SESSION_SEEDS_TOTAL = Counter(
    "linkedin_mcp_session_seeds_total",
    "Session LinkedIn injectée depuis le fichier dans le profil du navigateur "
    "(profil sans li_at valide) — chaque hausse = LinkedIn a supprimé la session "
    "ou le profil a été remis à zéro",
)


def record_session_seed(seeded: bool) -> None:
    """Compte un amorçage de session ; une session conservée ne compte pas."""
    if seeded:
        SESSION_SEEDS_TOTAL.inc()
```

- [ ] **Step 3 : brancher dans le serveur**

Dans `linkedin_mcp/server.py`, ligne 19, remplacer `from .metrics import track_tool_calls` par `from .metrics import record_session_seed, track_tool_calls`, puis remplacer les lignes 488-495 par :

```python
    try:
        await _browser_manager.start()
        await _browser_manager.load_session(session_path)
    except Exception as exc:
        _browser_manager = None
        raise _playwright_start_error(exc) from exc
    record_session_seed(getattr(_browser_manager, "session_seeded", False))
    _browser_initialized = True
    if getattr(_browser_manager, "session_seeded", False):
        logger.warning(
            "Navigateur initialisé : session LinkedIn AMORCÉE depuis %s "
            "(le profil n'en avait pas de valide)", session_path
        )
    else:
        logger.info("Navigateur initialisé : session LinkedIn du profil conservée")
    return _browser_manager
```

- [ ] **Step 4 : dépendance**

Dans `pyproject.toml` l.17 : `"linkedin-playwright-scraper>=4.6.0",`, puis `uv lock --upgrade-package linkedin-playwright-scraper`.
Run : `uv run python -c "import importlib.metadata as m; print(m.version('linkedin-playwright-scraper'))"`
Expected : `4.6.0`.

- [ ] **Step 5 : alerte et test promtool**

Ajouter à `kubernetes/prometheusrule.yaml`, dans `rules:` après `LinkedinMcpDown` :

```yaml
        - alert: LinkedinSessionSeeded
          # Hausse du compteur = le profil du Chromium dédié n'avait plus de li_at
          # valide : LinkedIn a supprimé la session (déconnexion forcée) ou le profil
          # a été remis à zéro. La session amorcée vient du Sealed Secret, souvent
          # périmée : à renouveler dans le Chromium dédié (docs/session-linkedin.md).
          expr: increase(linkedin_mcp_session_seeds_total[15m]) > 0
          labels:
            severity: warning
          annotations:
            summary: "linkedin-mcp a dû réamorcer la session LinkedIn"
            description: "Le profil du Chromium dédié n'avait plus de session LinkedIn valide : session réinjectée depuis le Sealed Secret. Vérifier la connexion et la renouveler si besoin (docs/session-linkedin.md, « Renouveler la session »)."
```

Régénérer : `yq '.spec | {"groups": .groups}' kubernetes/prometheusrule.yaml > kubernetes/prometheusrule.rules.yaml`

Ajouter à `kubernetes/prometheusrule.test.yaml`, sous `tests:` :

```yaml
  # Compteur stable -> silence ; hausse -> alerte immédiate (pas de for:).
  - interval: 1m
    input_series:
      - series: 'linkedin_mcp_session_seeds_total{namespace="linkedin-mcp",pod="linkedin-mcp-x"}'
        values: '0x10 1x10'
    alert_rule_test:
      - eval_time: 9m
        alertname: LinkedinSessionSeeded
        exp_alerts: []
      - eval_time: 12m
        alertname: LinkedinSessionSeeded
        exp_alerts:
          - exp_labels:
              severity: warning
              namespace: linkedin-mcp
              pod: linkedin-mcp-x
            exp_annotations:
              summary: "linkedin-mcp a dû réamorcer la session LinkedIn"
              description: "Le profil du Chromium dédié n'avait plus de session LinkedIn valide : session réinjectée depuis le Sealed Secret. Vérifier la connexion et la renouveler si besoin (docs/session-linkedin.md, « Renouveler la session »)."
```

Run : `uv run python test_session_seed_metric.py && promtool test rules kubernetes/prometheusrule.test.yaml`
Expected : deux lignes `OK`, puis `SUCCESS`.

- [ ] **Step 6 : commit (sans push)**

```bash
git add pyproject.toml uv.lock linkedin_mcp/metrics.py linkedin_mcp/server.py test_session_seed_metric.py kubernetes/prometheusrule*.yaml
git commit -m "feat(session): compter et alerter les réamorçages de session LinkedIn

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

## Task 4 : instance Chromium dédiée sur `gpu-node` (`k3s-homelab`)

**Files :**
- Create : `~/workspace/k3s-homelab/ansible/vars/chromium-cdp-linkedin.yml`
- Modify : `~/workspace/k3s-homelab/ansible/playbooks/install-chromium-cdp-host.yml` (liste `systemd_cgroup_metrics_units`, l.79-81)

**Interfaces :**
- Produces : CDP joignable sur `http://192.168.1.154:9242` depuis les pods.

- [ ] **Step 1 : ports libres**

Run : `ssh vinz@192.168.1.154 'ss -ltn | grep -E ":(9242|9243)\b" || echo libres'`
Expected : `libres`.

- [ ] **Step 2 : fichier de variables** (modèle : `ansible/vars/chromium-cdp-betclic-info.yml`)

```yaml
---
# Instance Chromium CDP « linkedin » sur gpu-node : navigateur DÉDIÉ à linkedin-mcp, dont le profil
# porte la session LinkedIn (source de vérité de la session, plan linkedin-mcp du 2026-10-07).
#
# Ne pas la partager : c'est le profil commun avec OpenClaw (chromium-cdp, 9222) et la réinjection
# des cookies du Sealed Secret à chaque relance qui faisaient mourir la session (post-mortem 045,
# incident du 2026-10-07). Aucun autre client CDP que linkedin-mcp.
#
# Lancer :
#   ansible-playbook ansible/playbooks/install-chromium-cdp-host.yml -i ansible/inventory/gpu.yml \
#     -e @ansible/vars/chromium-cdp-linkedin.yml
chromium_cdp_instance: chromium-cdp-linkedin
chromium_cdp_user: linkedin-browser
chromium_cdp_data_dir: "{{ data_root }}/volumes/linkedin-browser"
chromium_cdp_description: "Chromium (snap) CDP fenêtré sous Xvfb — session LinkedIn (linkedin-mcp) + proxy socat"
chromium_cdp_port: 9242          # exposé sur le LAN par socat (pods k3s, tunnel SSH pour la connexion)
chromium_cdp_internal_port: 9243 # port CDP Chromium, localhost uniquement
# Pas de redémarrage préventif : linkedin-mcp garde un onglet garé + l'onglet de la feed session,
# il n'en accumule pas. Sonde de vie et MemoryMax couvrent les pannes réelles.
chromium_cdp_preventive_restart: false
```

- [ ] **Step 3 : métriques cgroup**

Dans `install-chromium-cdp-host.yml`, ajouter `- chromium-cdp-linkedin.service` à la liste `systemd_cgroup_metrics_units`, après `chromium-cdp-betclic-info.service`.

- [ ] **Step 4 : dry-run puis application**

Run : `cd ~/workspace/k3s-homelab && ansible-playbook ansible/playbooks/install-chromium-cdp-host.yml -i ansible/inventory/gpu.yml -e @ansible/vars/chromium-cdp-linkedin.yml --check --diff`, puis la même commande sans `--check`.
Expected : service `chromium-cdp-linkedin` actif. Les services `chromium-cdp` et `chromium-cdp-betclic-info` ne sont pas modifiés : le diff ne porte que sur les fichiers de la nouvelle instance et sur la liste des métriques cgroup.

- [ ] **Step 5 : vérifier depuis un pod**

Run : `KUBECONFIG=~/.kube/config-k3s kubectl -n linkedin-mcp exec deploy/linkedin-mcp -- python -c "import urllib.request,json;print(json.load(urllib.request.urlopen('http://192.168.1.154:9242/json/version'))['Browser'])"`
Expected : `Chrome/1xx…`, et non `HeadlessChrome`.

- [ ] **Step 6 : commit (sans push)**

```bash
git add ansible/vars/chromium-cdp-linkedin.yml ansible/playbooks/install-chromium-cdp-host.yml
git commit -m "feat(chromium-cdp): instance dédiée à linkedin-mcp sur gpu-node (9242/9243)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

## Task 5 : basculer `linkedin-mcp` et documenter (ADR-005, runbook)

**Files :**
- Modify : `~/workspace/linkedin-mcp/kubernetes/deployment.yaml:1-3, 55-69` (commentaires et `LINKEDIN_CDP_URL`)
- Modify : `~/workspace/linkedin-mcp/docs/session-linkedin.md` (section « Renouveler la session », l.116-128)
- Create : `~/workspace/linkedin-mcp/docs/adr/005-profil-chromium-dedie-amorcage-unique.md`
- Modify : `~/workspace/linkedin-mcp/docs/adr/README.md` (index)

**Interfaces :**
- Consumes : tâches 3 (image avec 4.6.0) et 4 (CDP sur 9242).

- [ ] **Step 1 : URL CDP**

Dans `deployment.yaml`, remplacer la valeur de `LINKEDIN_CDP_URL` par `"http://192.168.1.154:9242"`, et réécrire le commentaire au-dessus :

```yaml
            # Chromium DÉDIÉ à linkedin-mcp sur gpu-node (instance chromium-cdp-linkedin,
            # k3s-homelab ansible/vars/chromium-cdp-linkedin.yml) : son profil porte la
            # session LinkedIn et n'est partagé avec personne (ADR-005, incident
            # 2026-10-07). IP et non nom DNS : Chrome rejette un en-tête Host qui n'est
            # ni une IP ni localhost (HTTP 500).
```

Mettre à jour les lignes 2-3 de l'en-tête : « scraping via CDP sur le Chromium dédié de gpu-node (LINKEDIN_CDP_URL, ADR-005) ».

- [ ] **Step 2 : runbook de renouvellement**

Dans `docs/session-linkedin.md`, remplacer le paragraphe « **Procédure** » de « Renouveler la session » par :

````markdown
**Procédure (k3s, depuis le 2026-10-07)** : se connecter **dans le Chromium dédié de gpu-node**, jamais
sur le Mac. Une session créée sur un navigateur puis utilisée depuis un autre est révoquée par LinkedIn
en quelques minutes (incident du 2026-10-07, ADR-005).

1. Tunnel : `ssh -N -L 9243:127.0.0.1:9243 vinz@192.168.1.154` (port CDP interne de l'instance).
2. Sur le Mac, dans Chrome : `chrome://inspect/#devices` → *Configure…* → ajouter `localhost:9243`.
3. Sous *Remote Target*, cliquer *inspect* sur l'onglet du Chromium dédié : DevTools affiche la page
   en direct (screencast) et transmet clavier et souris. Dans l'onglet *Console*, taper
   `location.href = "https://www.linkedin.com/login"`.
4. Se connecter (e-mail, mot de passe, 2FA) dans la vue, jusqu'à l'affichage du fil.
5. Fermer DevTools et le tunnel. Rien à redéployer : le profil porte la session.
6. Contrôle : `kubectl -n linkedin-mcp logs deploy/linkedin-mcp | grep -i session` ne doit montrer
   aucun « AMORCÉE » après la connexion. Lancer un `scrape_post` de test.

**Secours, profil vierge** (nouveau nœud, profil supprimé) : le pod amorce le profil depuis le Sealed
Secret. Sa session est souvent périmée : refaire aussitôt la procédure ci-dessus.

Le fichier local du Mac (`just session`) ne sert plus qu'au développement local. Ne pas le sceller vers
k3s s'il a servi sur le Mac.
````

- [ ] **Step 3 : ADR-005**

Créer `docs/adr/005-profil-chromium-dedie-amorcage-unique.md` :

```markdown
# ADR-005 : profil Chromium dédié et amorçage unique de la session LinkedIn

- **Date** : 2026-10-07
- **Statut** : accepté
- **Remplace partiellement** : ADR-017 de k3s-homelab (Chromium partagé `chromium-cdp-host.openclaw`)

## Contexte

Le 2026-10-07, après un redémarrage du parc, la session LinkedIn est morte (premier échec à 12:30),
puis une session neuve a été révoquée en moins de 6 minutes. Trois causes se combinaient :

- le profil du Chromium était partagé avec OpenClaw ;
- à chaque relance du navigateur, `load_session()` réinjectait **sans effacer** les cookies figés du
  Sealed Secret par-dessus ceux que LinkedIn avait rafraîchis ;
- la session avait été créée et utilisée sur le Mac avant d'être injectée.

Le contexte isolé (`new_context`) reste exclu : chaque contexte neuf déclenche une vérification de
sécurité, comme lors de l'incident du 2026-09-04.

## Décision

1. Instance Chromium **dédiée** `chromium-cdp-linkedin` sur gpu-node (9242/9243), profil non partagé,
   sans redémarrage préventif.
2. Le **profil fait foi**. `linkedin-playwright-scraper` ≥ 4.6.0 n'injecte le fichier de session que si
   le profil n'a pas de `li_at` valide, après effacement des cookies LinkedIn.
3. La session se crée et se renouvelle **dans ce Chromium**, via DevTools par tunnel SSH. Le Sealed
   Secret n'est plus qu'un amorçage de secours.
4. Chaque amorçage est compté (`linkedin_mcp_session_seeds_total`) et alerte (`LinkedinSessionSeeded`).

## Conséquences

- Les redémarrages du pod, du navigateur ou du nœud ne touchent plus la session.
- Le renouvellement demande un tunnel SSH et Chrome sur le Mac, et non plus `just session` puis un
  rescellement.
- Un Chromium de plus sur gpu-node (quelques centaines de Mo de RAM).
- Le profil partagé d'OpenClaw garde d'anciens cookies LinkedIn inutiles. Nettoyage facultatif.
```

Ajouter la ligne `| [005](005-profil-chromium-dedie-amorcage-unique.md) | Profil Chromium dédié et amorçage unique de la session LinkedIn | 2026-10-07 | Accepté |` à l'index `docs/adr/README.md`, au format des lignes existantes.

- [ ] **Step 4 : commit (sans push)**

```bash
git add kubernetes/deployment.yaml docs/session-linkedin.md docs/adr/005-profil-chromium-dedie-amorcage-unique.md docs/adr/README.md
git commit -m "feat(k3s): linkedin-mcp sur le Chromium dédié de gpu-node (ADR-005)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

## Task 6 : mise en service et validation (avec l'utilisateur)

**Interfaces :**
- Consumes : tâches 1 à 5.

- [ ] **Step 1 : feu vert et push**

Demander le feu vert, puis pousser `k3s-homelab` (tâche 4) et `linkedin-mcp` (tâches 3 et 5). Attendre l'image CI de `linkedin-mcp`, puis la synchronisation d'Argo CD : `kubectl -n argocd get application linkedin-mcp -o jsonpath='{.status.sync.revision}'` doit renvoyer le dernier commit.

- [ ] **Step 2 : premier démarrage, amorçage attendu**

Run : `kubectl -n linkedin-mcp logs deploy/linkedin-mcp | grep -i -E "session|amorc"`
Expected : au premier appel d'outil, « session LinkedIn AMORCÉE » (profil vierge). L'alerte `LinkedinSessionSeeded` part sur Telegram, ce qui est attendu et vérifie au passage la chaîne d'alerte.

- [ ] **Step 3 : connexion dans le Chromium dédié** (utilisateur, runbook de la tâche 5, étapes 1 à 5)

- [ ] **Step 4 : contrôle de la session dans le profil** (noms et dates seulement, jamais les valeurs)

Run (sur gpu-node, en root) : le script Python de lecture de `Default/Cookies` utilisé le 2026-10-07, avec le profil `/data/volumes/linkedin-browser/profile`.
Expected : un `li_at` créé après la connexion, avec une date d'expiration dans le futur.

- [ ] **Step 5 : scrape réel**

Un `scrape_post` sur une URL `/feed/update/urn:li:activity:…` connue, via la passerelle LiteLLM (`/mcp-rest/tools/call`, `server_id` de `linkedin_mcp`).
Expected : un tableau JSON avec `text` non vide, sans « Not logged in ».

- [ ] **Step 6 : redémarrage du pod, aucune réinjection**

Run : `kubectl -n linkedin-mcp rollout restart deploy/linkedin-mcp`, puis un `scrape_post`, puis lire les logs.
Expected : « session LinkedIn du profil conservée », `linkedin_mcp_session_seeds_total` inchangé, et le scrape réussit.

- [ ] **Step 7 : redémarrage du navigateur, la session tient**

Run : `ssh vinz@192.168.1.154 'sudo systemctl restart chromium-cdp-linkedin'`, attendre 30 s, puis un `scrape_post`.
Expected : le scrape réussit, et le log indique « conservée ».

- [ ] **Step 8 : 24 h d'usage réel**

Le lendemain : run du cron `linkedin-feed-daily` en succès, `linkedin-sync` sans erreur d'authentification, compteur d'amorçages stable depuis l'étape 3. Consigner le résultat dans ADR-005 (section « Validation ») et commiter.
