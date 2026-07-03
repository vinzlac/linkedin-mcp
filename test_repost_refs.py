"""Unit tests for repost reference parsing."""
from linkedin_mcp.linkedin.repost import (
    activity_id_from_post_ref,
    compkey_from_post_ref,
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


if __name__ == "__main__":
    test_activity_id_from_url()
    test_compkey_from_urn()
    test_compkey_plain_urn()
    print("✅ test_repost_refs OK")
