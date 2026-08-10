"""Tests for canonical category path normalization."""

from scraper.urlutils import (
    normalize_category_path,
    category_slug_from_path,
    parent_path,
    path_level,
    path_segments,
)


def test_normalize_simple_path():
    assert (
        normalize_category_path("https://www.soundimports.eu/en/home-audio/speakers/")
        == "/en/home-audio/speakers/"
    )


def test_trailing_slash_normalized():
    assert normalize_category_path("/en/home-audio") == "/en/home-audio/"
    assert normalize_category_path("/en/home-audio/") == "/en/home-audio/"


def test_query_and_fragment_stripped():
    assert (
        normalize_category_path("https://www.soundimports.eu/en/speakers/?page=2#top")
        == "/en/speakers/"
    )


def test_session_id_stripped():
    assert (
        normalize_category_path(
            "https://www.soundimports.eu/en/amplifiers/;jsessionid=ABC123"
        )
        == "/en/amplifiers/"
    )


def test_lowercased_and_punctuation_cleaned():
    assert normalize_category_path("/EN/Dummy/Über-Gads/") == "/en/dummy/uber-gads/"
    assert normalize_category_path("/en/Devices-&-More/") == "/en/devices-more/"


def test_double_slashes_collapsed():
    assert normalize_category_path("https://www.soundimports.eu/en//speakers//") == (
        "/en/speakers/"
    )


def test_relative_path_without_base():
    assert normalize_category_path("/en/foo/bar/") == "/en/foo/bar/"


def test_relative_path_resolved_with_base():
    assert (
        normalize_category_path(
            "speakers/bookshelf/", base_url="https://www.soundimports.eu/en/"
        )
        == "/en/speakers/bookshelf/"
    )


def test_encoded_slash_does_not_change_hierarchy():
    assert normalize_category_path("/en/foo%2Fbar/baz/") == "/en/foo-bar/baz/"


def test_empty_invalid_input():
    assert normalize_category_path("") == ""
    assert normalize_category_path(None) == ""


def test_duplicate_slugs_keep_distinct_paths():
    p1 = normalize_category_path(
        "https://www.soundimports.eu/en/home-audio/amplifiers/switches/"
    )
    p2 = normalize_category_path(
        "https://www.soundimports.eu/en/accessories/electromechanics/switches/"
    )
    assert p1 != p2
    assert p1 == "/en/home-audio/amplifiers/switches/"
    assert p2 == "/en/accessories/electromechanics/switches/"


def test_category_slug_from_path():
    assert category_slug_from_path("/en/home-audio/speakers/") == "speakers"
    assert category_slug_from_path("/en/home-audio/") == "home-audio"
    assert category_slug_from_path("/") == ""


def test_parent_path():
    assert parent_path("/en/home-audio/speakers/") == "/en/home-audio/"
    assert parent_path("/en/home-audio/") == "/en/"
    assert parent_path("/en/") is None


def test_path_level():
    assert path_level("/en/home-audio/speakers/") == 3
    assert path_level("/en/") == 1
    assert path_level("/en/home-audio/") == 2


def test_path_segments():
    assert path_segments("/en/foo/bar/") == ["en", "foo", "bar"]
    assert path_segments("/") == []
