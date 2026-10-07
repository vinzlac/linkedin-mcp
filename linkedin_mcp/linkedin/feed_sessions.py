"""Registre des sessions de fil (spec lots de scraping, R55-R60).

Une session de fil possède son propre onglet : c'est ce qui lui permet de
garder sa position dans le fil entre deux appels, alors que l'onglet partagé
des autres outils est garé sur about:blank après chacun (2026-09-21).

Module pur, sans Playwright : le serveur lui confie la page et le scraper,
et se charge de les fermer. L'horloge est injectable pour les tests.
"""
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple


class UnknownFeedSessionError(RuntimeError):
    """Session expirée, remplacée, ou perdue au redémarrage du serveur (R60)."""

    def __init__(self, session_id: str):
        super().__init__(f"session de fil inconnue : {session_id}")


@dataclass
class FeedSession:
    session_id: str
    page: Any
    scraper: Any
    last_used: float
    posts_returned: int = 0


class FeedSessionRegistry:
    """Au plus une session (R58) ; fermeture après inactivité (R59)."""

    def __init__(self, idle_ttl_s: float = 600.0, clock: Callable[[], float] = time.monotonic):
        self._idle_ttl_s = idle_ttl_s
        self._clock = clock
        self._sessions: Dict[str, FeedSession] = {}

    def __len__(self) -> int:
        return len(self._sessions)

    def open(self, page: Any, scraper: Any) -> Tuple[FeedSession, Optional[FeedSession]]:
        """Enregistre une nouvelle session ; rend aussi celle qu'elle remplace,
        que l'appelant doit fermer (worker planté sans `end`, R58)."""
        remplacees = self.pop_all()
        sess = FeedSession(uuid.uuid4().hex, page, scraper, self._clock())
        self._sessions[sess.session_id] = sess
        return sess, (remplacees[0] if remplacees else None)

    def get(self, session_id: str) -> FeedSession:
        sess = self._sessions.get(session_id)
        if sess is None:
            raise UnknownFeedSessionError(session_id)
        sess.last_used = self._clock()
        return sess

    def touch(self, sess: FeedSession) -> None:
        """Marque la session comme utilisée maintenant (fin d'un appel)."""
        sess.last_used = self._clock()

    def active_since(self, window_s: float) -> Optional[FeedSession]:
        """La session utilisée il y a moins de `window_s`, s'il y en a une :
        elle appartient sans doute à un run en cours, qu'il ne faut pas
        déposséder (deux runs simultanés, 2026-10-06)."""
        limite = self._clock() - window_s
        return next((s for s in self._sessions.values() if s.last_used >= limite), None)

    def pop(self, session_id: str) -> Optional[FeedSession]:
        return self._sessions.pop(session_id, None)

    def pop_all(self) -> List[FeedSession]:
        sessions = list(self._sessions.values())
        self._sessions.clear()
        return sessions

    def pop_expired(self) -> List[FeedSession]:
        limite = self._clock() - self._idle_ttl_s
        expirees = [s for s in self._sessions.values() if s.last_used < limite]
        for s in expirees:
            del self._sessions[s.session_id]
        return expirees
