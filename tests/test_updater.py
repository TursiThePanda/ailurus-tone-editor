"""Linux has no Sparkle: the app only tells the user that a newer release exists."""
from ailurus_tone.updater import check_latest


def rel(tag):
    return lambda: {"tag_name": tag, "html_url": f"https://github.com/x/releases/tag/{tag}"}


def test_newer_release_is_reported():
    r = check_latest("0.3.0", fetch=rel("v0.3.1"))
    assert r == {"latest": "0.3.1", "url": "https://github.com/x/releases/tag/v0.3.1", "newer": True}
    assert check_latest("0.3.0", fetch=rel("v0.10.0"))["newer"]         # numbers, not text


def test_same_or_older_is_not():
    assert not check_latest("0.3.0", fetch=rel("v0.3.0"))["newer"]
    assert not check_latest("0.4.0", fetch=rel("v0.3.9"))["newer"]
    assert not check_latest("0.3.0", fetch=lambda: {})["newer"]
