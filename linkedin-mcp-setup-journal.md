# LinkedIn MCP Server — Journal de mise en place

## Contexte

Mise en place d'un serveur MCP LinkedIn pour Claude Desktop permettant de poster des messages LinkedIn directement depuis Claude.

- **Date** : 12-13 mars 2026
- **Environnement** : MacBook Pro, utilisateur `vinz`, macOS
- **Package utilisé** : `linkedin-mcp` v0.1.7 (FilippTrigub)
- **Repo** : https://github.com/FilippTrigub/linkedin-mcp
- **Workspace local** : `/Users/vinz/workspace/linkedin-mcp`

---

## État final — Ce qui fonctionne

```json
{
  "mcpServers": {
    "linkedin-mcp": {
      "command": "/opt/homebrew/bin/uv",
      "args": [
        "--directory",
        "/Users/vinz/workspace/linkedin-mcp",
        "run",
        "python",
        "-c",
        "from linkedin_mcp.server import main; main()"
      ],
      "env": {
        "LINKEDIN_CLIENT_ID": "78bw9yiq66qnrm",
        "LINKEDIN_CLIENT_SECRET": "<SECRET>",
        "LINKEDIN_REDIRECT_URI": "http://localhost:3000/callback"
      }
    }
  }
}
```

Les outils exposés par le serveur :
- `authenticate` — Lance le flow OAuth2 LinkedIn (ouvre le navigateur)
- `create_post` — Poste du texte sur LinkedIn avec options média et visibilité

---

## Prérequis accomplis

### 1. LinkedIn Developer App

- App créée sur https://www.linkedin.com/developers/apps
- **App name** : Personal
- **Client ID** : `78bw9yiq66qnrm`
- **Company Page** : `linkedin.com/company/vincent-lacoste/` (créée spécifiquement pour valider l'app — LinkedIn n'accepte pas les profils `/in/...`)
- **Privacy Policy URL** : `https://www.code-advisors.site/fr`
- **Produit activé** : "Share on LinkedIn" (Default Tier, accès immédiat)
- **Redirect URL configurée** : `http://localhost:3000/callback`
- **Vérification company** : faite via "Generate URL" → ouverture dans le même navigateur → "I'm done"

### 2. Fichier .env

Créé dans `/Users/vinz/workspace/linkedin-mcp/.env` :

```env
LINKEDIN_CLIENT_ID=78bw9yiq66qnrm
LINKEDIN_CLIENT_SECRET=<SECRET>
LINKEDIN_REDIRECT_URI=http://localhost:3000/callback
```

### 3. Dépendances

- `uv` installé via `brew install uv`
- Chemin absolu : `/opt/homebrew/bin/uv`

---

## Problèmes rencontrés et solutions

### Problème 1 — `Failed to spawn process: No such file or directory`

**Cause** : Claude Desktop utilisait `"command": "linkedin-mcp"` sans chemin absolu. L'exécutable installé par `pipx` dans `/Users/vinz/.local/bin/linkedin-mcp` n'était pas dans le PATH de Claude Desktop.

**Solution** : Utiliser le chemin absolu dans la config :
```json
"command": "/Users/vinz/.local/bin/linkedin-mcp"
```

---

### Problème 2 — `ModuleNotFoundError: No module named 'linkedin_mcp'`

**Cause** : Bug de packaging du package PyPI `linkedin-mcp`. Le `pyproject.toml` déclarait :
```toml
packages = ["src/linkedin", "src/config"]
```
Mais le code source réel est dans `linkedin_mcp/` à la racine du repo — ces dossiers `src/` n'existent pas. Résultat : seul le `.dist-info` était installé, pas le code.

**Tentatives échouées** :
- `pipx uninstall` + réinstall avec Python 3.13 → même bug
- Installation depuis GitHub via `pipx install git+https://...` → même bug
- Ajout de `PYTHONPATH` → ne résout pas le problème de packaging

**Solution finale** :
1. Cloner le repo localement :
   ```bash
   git clone https://github.com/FilippTrigub/linkedin-mcp.git ~/workspace/linkedin-mcp
   ```
2. Corriger le `pyproject.toml` :
   ```toml
   # Avant (cassé)
   packages = ["src/linkedin", "src/config"]
   
   # Après (corrigé)
   packages = ["linkedin_mcp"]
   ```
   Commande utilisée (sed ne fonctionne pas avec les caractères spéciaux sur macOS) :
   ```bash
   python3 -c "
   content = open('/Users/vinz/workspace/linkedin-mcp/pyproject.toml').read()
   content = content.replace('packages = [\"src/linkedin\", \"src/config\"]', 'packages = [\"linkedin_mcp\"]')
   open('/Users/vinz/workspace/linkedin-mcp/pyproject.toml', 'w').write(content)
   print('Done')
   "
   ```

---

### Problème 3 — Mismatch de version du protocole MCP

**Cause** : Le `uv.lock` du repo fixait `mcp==1.2.0`, mais Claude Desktop utilise le protocole `2025-11-25` qui nécessite `mcp >= 1.6.0`. Le serveur crashait silencieusement après le message `initialize`.

**Symptôme dans les logs** :
```
Message from client: {"method":"initialize","params":{"protocolVersion":"2025-11-25",...}}
Server transport closed unexpectedly
```

**Solution** :
```bash
cd ~/workspace/linkedin-mcp
uv add "mcp[cli]>=1.6.0"
```

---

### Problème 4 — `mcp.run()` sans transport explicite

**Cause** : La fonction `main()` appelait `mcp.run()` sans spécifier `transport="stdio"`. Selon les versions de FastMCP, cela peut causer des comportements inattendus.

**Solution** : Patch du `server.py` :
```bash
python3 -c "
content = open('/Users/vinz/workspace/linkedin-mcp/linkedin_mcp/server.py').read()
content = content.replace('mcp.run()', 'mcp.run(transport=\"stdio\")')
open('/Users/vinz/workspace/linkedin-mcp/linkedin_mcp/server.py', 'w').write(content)
print('Done')
"
```

---

### Problème 5 — Le .env n'est pas chargé par Claude Desktop

**Cause** : Le `settings.py` utilise `env_file = ".env"` (chemin relatif). Claude Desktop ne lance pas le serveur depuis le répertoire du projet, donc le `.env` n'est jamais trouvé.

**Solution** : Passer les credentials directement dans le bloc `env` du `claude_desktop_config.json` ET utiliser `--directory` dans les args `uv` pour forcer le répertoire de travail.

---

### Problème 6 — `sed` ne fonctionne pas avec les caractères spéciaux sur macOS

**Symptôme** :
```
sed: can't read s/packages = \["src\/linkedin"...
```

**Cause** : `sed` sur macOS (BSD) gère différemment les caractères spéciaux, surtout les guillemets et crochets dans les expressions.

**Solution** : Utiliser `python3 -c` pour les remplacements de texte complexes.

---

### Problème 6 — Méthode de lancement

**Cause** : Lancer avec `uv run -m linkedin_mcp.server` vs `uv run python -c "from linkedin_mcp.server import main; main()"` donne des comportements différents selon comment FastMCP initialise le transport stdio.

**Solution finale retenue** :
```json
"args": [
  "--directory", "/Users/vinz/workspace/linkedin-mcp",
  "run", "python", "-c",
  "from linkedin_mcp.server import main; main()"
]
```

---

## Script de test

Un script `test_linkedin_mcp.sh` a été créé dans le workspace pour valider le bon fonctionnement du serveur avant de relancer Claude Desktop. Il simule exactement les messages MCP qu'envoie Claude Desktop (`initialize` + `tools/list`) et vérifie que la réponse JSON est correcte.

```bash
bash /Users/vinz/workspace/linkedin-mcp/test_linkedin_mcp.sh
```

Résultat attendu :
```
✅ .env trouvé
✅ Credentials OK
✅ Serveur répond correctement !
--- Outils disponibles : authenticate, create_post ---
✅ Serveur MCP opérationnel
```

---

## Prochaines étapes

1. **Redémarrer Claude Desktop** avec la config finale
2. **S'authentifier** : demander à Claude "Authentifie-toi sur LinkedIn" → flow OAuth2 → navigateur s'ouvre → autoriser → token sauvegardé dans `linkedin_mcp/tokens/`
3. **Tester un post** : "Poste un message LinkedIn pour annoncer ma certification X"
4. **Note** : Le token OAuth expire après **2 mois** — il faudra refaire l'authentification à cette échéance

---

## Fichiers modifiés dans le repo

| Fichier | Modification |
|---|---|
| `pyproject.toml` | `packages = ["linkedin_mcp"]` au lieu de `["src/linkedin", "src/config"]` |
| `linkedin_mcp/server.py` | `mcp.run(transport="stdio")` au lieu de `mcp.run()` |
| `uv.lock` | Mis à jour avec `mcp >= 1.6.0` via `uv add "mcp[cli]>=1.6.0"` |
| `.env` | Créé avec les credentials LinkedIn |
| `test_linkedin_mcp.sh` | Créé — script de test local |