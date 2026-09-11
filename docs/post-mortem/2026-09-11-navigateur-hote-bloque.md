# Navigateur hôte bloqué : `/json/version` répond, `/json/list` non

**Date** : 2026-09-10 et 2026-09-11
**Impact** : quatre runs de `linkedin-feed-scrapping` perdus, dont le cron de 20:00 du 10/09.
**Résolution** : `sudo systemctl restart chromium-cdp.service` sur `geekom-as6`.

## Ce qui s'est passé

Trois appels d'outils ont bloqué jusqu'au délai du client (180 s), avec pour
seule trace :

```
15:26:33  Like LinkedIn post_url=…7503445376373510145
15:26:38  WARNING - Sonde navigateur Playwright: instance morte ()
15:26:38  WARNING - Navigateur Playwright mis en cache mort/déconnecté — relance automatique
15:26:38  Browser disconnected (CDP)
15:29:33  Request 3 cancelled - duplicate response suppressed
```

La relance automatique de `_get_browser()` n'est jamais revenue. Le lendemain,
le même blocage s'est produit sur `scrape_feed`, cette fois avec le message
complet :

```
BrowserType.connect_over_cdp: Timeout 180000ms exceeded.
  - <ws preparing> retrieving websocket url from http://10.43.230.242:9222
  - <ws connecting> ws://…/devtools/browser/530d1fae…
  - <ws connected>  ws://…
```

## Cause

Le Chromium de l'hôte était **à moitié vivant** :

```
curl http://127.0.0.1:9222/json/version  →  Chrome/152.0.7977.64   ✅
curl http://127.0.0.1:9222/json/list     →  (rien, expire)          ❌
```

`/json/version` est servi sans énumérer les cibles ; `/json/list` doit les
parcourir — et c'est exactement ce que fait `connect_over_cdp` à la connexion.
D'où un WebSocket qui se connecte puis une poignée de main qui n'aboutit jamais.

La veille, le symptôme était différent pour la même conséquence : les processus
`chrome` étaient en état `D` (sommeil ininterruptible) depuis le démarrage de
l'hôte, avec un load average à 22 et ~290 Mo/s de lecture nvme. Là, le
redémarrage du service ne tient pas tant que l'hôte est saturé.

## Leçons

### 1. `/json/version` n'est pas une sonde de santé

Il répond alors que le navigateur est inutilisable. **Sonder `/json/list`** :
c'est le seul appel qui exerce le même chemin que Playwright.

### 2. « Navigateur mis en cache mort — relance automatique » induit en erreur

Le message annonce une relance qui, ici, ne pouvait pas aboutir : le cache
n'était pas le problème, le navigateur distant l'était. Un message qui dit ce
qu'on tente sans jamais dire si ça a marché envoie le diagnostic dans la
mauvaise direction — c'est le même défaut que celui corrigé en §A.5 n°2.

### 3. La relance devrait échouer vite

`connect_over_cdp` hérite d'un délai de 180 s. Sur un chemin de RÉCUPÉRATION,
c'est ajouter un blocage au blocage. Un délai court et un échec franc valent
mieux qu'une tentative qui tient la ligne.

## Ce qui reste à faire ici

### a. `LINKEDIN_CDP_URL` code en dur une ClusterIP

```
LINKEDIN_CDP_URL=http://10.43.230.242:9222
```

C'est l'IP du Service `chromium-cdp-host` (sans sélecteur, Endpoints manuels
vers `192.168.1.153:9222`). Si le Service est recréé, l'IP change et
`linkedin-mcp` pointe dans le vide **sans rien dire**. Utiliser le nom DNS.

À noter pour les prochains diagnostics : ce n'est **pas** le pod
`openclaw/chromium-cdp-pod` qui est utilisé, mais un Chromium snap sous Xvfb sur
l'hôte. Redémarrer le pod ne change rien — erreur commise deux fois.

### b. La famille d'URN est devinée par cascade, et c'est par post

Sur sept engagements de la semaine : 3 ont marché en `activity`, 3 ont eu besoin
du repli `ugcPost`, 1 a échoué sur les trois formes. Chaque mauvais départ coûte
10 à 35 s (trois essais d'UI avant de basculer).

Côté appelant, la question a été tranchée : transporter la famille scrapée
n'aiderait pas, puisque **tous** les posts sont collectés en `activity`, y
compris ceux où cette forme échoue (voir `identity.ts` dans
`linkedin-feed-scrapping`). La piste restante est **ici** : mémoriser par post
la forme qui a fonctionné, au lieu de repayer les trois essais à chaque action.

### c. `session_expired` en faux positif

Le 2026-09-04, quatre `Like KO : session_expired` d'affilée — puis un repost
réussit et le like passe deux minutes plus tard, sur la même session. Le
diagnostic journalise désormais l'URL observée et lequel des deux signaux
(`loginUrl` ou `sessionKeyInput`) a déclenché ; il reste à exploiter ces traces
au prochain cas pour corriger la sonde.

L'enjeu est concret : un `session_expired` fait abandonner la cascade d'URL, et
côté appelant il est classé `auth-required`, donc **sans aucun rejeu**. Un faux
positif coûte un engagement.
