# LinkedIn MCP Server — Ajout de l'outil `get_posts`

## Contexte du projet

Ce repo est un fork local de `linkedin-mcp` (FilippTrigub) patché pour fonctionner
avec Claude Desktop. Il expose des outils MCP permettant d'interagir avec LinkedIn.

### Patches déjà appliqués

- `pyproject.toml` : `packages = ["linkedin_mcp"]` (le repo original pointait vers
  des dossiers `src/` inexistants)
- `linkedin_mcp/server.py` : `mcp.run(transport="stdio")` (transport explicite)
- `uv.lock` : `mcp >= 1.6.0` (compatibilité protocole Claude Desktop `2025-11-25`)

### Outils MCP actuellement exposés

- `authenticate` — Flow OAuth2 LinkedIn, stocke le token dans `linkedin_mcp/tokens/`
- `create_post` — Poste du texte sur LinkedIn avec options média et visibilité

---

## Tâche

Ajouter un outil MCP `get_posts` qui récupère les posts LinkedIn récents de
l'utilisateur authentifié.

### Plan d'implémentation

1. **Lire le code existant** pour comprendre la structure :
   - `linkedin_mcp/server.py` — définition des outils MCP avec FastMCP
   - `linkedin_mcp/linkedin/auth.py` — gestion OAuth2 et stockage du token
   - `linkedin_mcp/linkedin/post.py` — appels API LinkedIn existants
   - `linkedin_mcp/config/settings.py` — configuration et endpoints

2. **Créer `linkedin_mcp/linkedin/reader.py`** — nouveau module pour la lecture :
   - Classe `PostReader` sur le modèle de `PostManager`
   - Méthode `get_posts(count: int = 10)` qui :
     - Charge le token depuis `linkedin_mcp/tokens/`
     - Récupère l'`id` utilisateur via `GET /v2/userinfo`
     - Appelle `GET /v2/ugcPosts?q=authors&authors=List(urn:li:person:{id})`
     - Retourne une liste de posts formatés (date, texte, visibilité, URN)
   - Gestion propre si non authentifié → message clair

3. **Modifier `linkedin_mcp/server.py`** — ajouter l'outil :
   ```python
   @mcp.tool()
   async def get_posts(count: int = 10) -> str:
       """Récupère les posts LinkedIn récents de l'utilisateur.
       
       Args:
           count: Nombre de posts à récupérer (défaut 10, max 50)
       
       Returns:
           Liste des posts formatés
       """
   ```

### Headers API LinkedIn requis

```python
headers = {
    "Authorization": f"Bearer {token}",
    "LinkedIn-Version": settings.LINKEDIN_VERSION,  # "202210"
    "X-Restli-Protocol-Version": settings.RESTLI_PROTOCOL_VERSION,  # "2.0.0"
    "Content-Type": "application/json"
}
```

### Endpoint à utiliser

```
GET https://api.linkedin.com/v2/ugcPosts
    ?q=authors
    &authors=List(urn:li:person:{person_id})
    &count={count}
    &sortBy=LAST_MODIFIED
```

---

## Validation

Après implémentation, lancer le script de test :

```bash
bash test_linkedin_mcp.sh
```

Vérifier que :
1. Le serveur démarre sans erreur
2. `get_posts` apparaît dans la liste des outils retournée par `tools/list`
3. L'outil accepte un paramètre optionnel `count`

---

## Mémoire

Les mémoires de ce projet sont dans `memory/MEMORY.md`.
Mets à jour ce fichier au fil de la conversation pour les informations de type `project` et `reference`.

---

## Notes importantes

- Ne pas modifier le `.env` ni les tokens existants
- Utiliser `httpx` (déjà dans les dépendances) pour les appels HTTP
- Suivre le même pattern que `PostManager` dans `post.py` pour la cohérence
- Si l'API retourne une erreur 401 → message "Non authentifié, lance authenticate d'abord"
- Si l'API retourne une erreur 403 → le scope `r_member_social` n'est pas activé
  sur l'app LinkedIn Developer (à documenter dans le message d'erreur)