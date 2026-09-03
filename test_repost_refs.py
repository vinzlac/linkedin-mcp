"""Unit tests for repost reference parsing."""
from linkedin_mcp.linkedin.repost import (
    activity_id_from_post_ref,
    canonical_post_url,
    compkey_from_post_ref,
    post_url_candidates,
)


def test_activity_id_from_url():
    url = "https://www.linkedin.com/feed/update/urn:li:activity:7478749906967408640/"
    assert activity_id_from_post_ref(url) == "7478749906967408640"


def test_compkey_from_urn():
    urn = "urn:li:compkey:expandedCJvw3rdmRsIKY4FMvv6BuN4oOkS9hvDVoAQc_e0OO-8"
    key = compkey_from_post_ref(urn)
    assert key is not None
    assert key.startswith("CJvw3rdmRsIKY4FMvv6BuN4")
    assert "expanded" not in key


def test_compkey_plain_urn():
    urn = "urn:li:compkey:cFY36nfVwInnXucbq5x6TfTKUPpRnR8-rLGEwGxh8lA"
    assert compkey_from_post_ref(urn) == "cFY36nfVwInnXucbq5x6TfTKUPpRnR8-rLGEwGxh8lA"


# --- BUG #2 (2026-09-03) : like_post / repost_post KO avec un URN en entrée ---
# Le id numérique d'un permalien /posts/{slug}-share-{id}-{suffix}/ est un share
# ou ugcPost id. Reconstruire /feed/update/urn:li:activity:{id}/ à partir de là
# tombe parfois sur une page sans barre d'action, d'où « bouton introuvable »
# alors que le post existe et que la session est valide. On tente donc les trois
# formes d'URN en cascade.

_ACTIVITY_ID = "7500824506219909120"
_POSTS_URL = (
    "https://www.linkedin.com/posts/thierry-templier-7ba726_mon-code-"
    f"share-{_ACTIVITY_ID}-mYgS/"
)


def test_post_url_candidates_from_bare_urn_covers_three_urn_forms():
    candidates = post_url_candidates(f"urn:li:activity:{_ACTIVITY_ID}")
    assert candidates == [
        f"https://www.linkedin.com/feed/update/urn:li:activity:{_ACTIVITY_ID}/",
        f"https://www.linkedin.com/feed/update/urn:li:ugcPost:{_ACTIVITY_ID}/",
        f"https://www.linkedin.com/feed/update/urn:li:share:{_ACTIVITY_ID}/",
    ]


def test_post_url_candidates_prefers_full_url_then_falls_back():
    candidates = post_url_candidates(_POSTS_URL)
    assert candidates[0] == _POSTS_URL
    assert f"/feed/update/urn:li:activity:{_ACTIVITY_ID}/" in candidates[1]
    assert len(candidates) == 4


def test_post_url_candidates_deduplicates_feed_update_input():
    url = f"https://www.linkedin.com/feed/update/urn:li:activity:{_ACTIVITY_ID}/"
    candidates = post_url_candidates(url)
    assert candidates[0] == url
    assert len(candidates) == len(set(candidates)) == 3


def test_post_url_candidates_empty_for_unparsable_ref():
    assert post_url_candidates("n'importe quoi") == []


def test_canonical_post_url_is_first_candidate():
    assert canonical_post_url(_POSTS_URL) == _POSTS_URL
    assert canonical_post_url(f"urn:li:activity:{_ACTIVITY_ID}") == (
        f"https://www.linkedin.com/feed/update/urn:li:activity:{_ACTIVITY_ID}/"
    )
    assert canonical_post_url("n'importe quoi") is None


if __name__ == "__main__":
    test_activity_id_from_url()
    test_compkey_from_urn()
    test_compkey_plain_urn()
    test_post_url_candidates_from_bare_urn_covers_three_urn_forms()
    test_post_url_candidates_prefers_full_url_then_falls_back()
    test_post_url_candidates_deduplicates_feed_update_input()
    test_post_url_candidates_empty_for_unparsable_ref()
    test_canonical_post_url_is_first_candidate()
    print("✅ test_repost_refs OK")
