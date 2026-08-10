"""Unit tests for the legacy-category backfill logic in migration 004.

The migration module is loaded directly so its pure helpers can be tested
without running Alembic (a full end-to-end Alembic run is validated
separately).
"""

import importlib.util
from pathlib import Path

import pytest

_MIG = Path("alembic/versions/004_category_identity_and_product_categories.py")


@pytest.fixture(scope="module")
def mig004():
    spec = importlib.util.spec_from_file_location("mig004", _MIG)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_normalize_path(mig004):
    norm = mig004._normalize_path
    assert norm("https://www.soundimports.eu/en/home-audio/speakers/") == (
        "/en/home-audio/speakers/"
    )
    assert (
        norm("/en/home-audio/amplifiers/switches/")
        == "/en/home-audio/amplifiers/switches/"
    )
    assert norm("https://www.soundimports.eu/en/home-audio/speakers/;sid=99?p=2") == (
        "/en/home-audio/speakers/"
    )
    assert norm("") == ""
    assert norm("https://www.soundimports.eu/en/") == "/en/"


def test_resolve_backfill_happy_path(mig004):
    categories = [
        (1, "speakers", True),
        (2, "amplifiers", True),
    ]
    products = [
        (10, "S1", "speakers"),
        (11, "S2", "amplifiers, speakers"),
    ]
    rows, unresolved, ambiguous, skipped_unresolved, skipped_ambiguous = (
        mig004._resolve_backfill(categories, products)
    )
    assert rows == {(10, 1), (11, 2), (11, 1)}
    assert unresolved == set()
    assert ambiguous == set()
    assert skipped_unresolved == 0
    assert skipped_ambiguous == 0


def test_resolve_backfill_ambiguous_slug_skipped(mig004):
    # two categories share the slug 'switches' -> references must be skipped
    categories = [
        (1, "switches", True),
        (2, "switches", True),
        (3, "single-slug", True),
    ]
    products = [
        (10, "S1", "switches"),
        (11, "S2", "switches,single-slug"),
    ]
    rows, unresolved, ambiguous, skipped_unresolved, skipped_ambiguous = (
        mig004._resolve_backfill(categories, products)
    )
    assert rows == {(11, 3)}
    assert ambiguous == {"switches"}
    assert unresolved == set()
    assert skipped_unresolved == 0
    assert skipped_ambiguous == 2


def test_resolve_backfill_unresolved_ref_reported(mig004):
    categories = [(1, "speakers", True)]
    products = [(10, "S1", "speakers"), (11, "S2", "does-not-exist")]
    rows, unresolved, ambiguous, skipped_unresolved, skipped_ambiguous = (
        mig004._resolve_backfill(categories, products)
    )
    assert rows == {(10, 1)}
    assert unresolved == {"does-not-exist"}
    assert ambiguous == set()
    assert skipped_unresolved == 1
    assert skipped_ambiguous == 0


def test_resolve_backfill_empty_inputs(mig004):
    rows, unresolved, ambiguous, skipped_unresolved, skipped_ambiguous = (
        mig004._resolve_backfill([], [])
    )
    assert rows == set()
    assert unresolved == set()
    assert ambiguous == set()
    assert skipped_unresolved == 0
    assert skipped_ambiguous == 0


def test_resolve_backfill_supports_legacy_ids_and_canonical_paths(mig004):
    categories = [
        (1, "speakers", True, "/en/home-audio/speakers/"),
        (2, "switches", True, "/en/a/switches/"),
        (3, "switches", True, "/en/b/switches/"),
    ]
    products = [
        (10, "S1", "1"),
        (11, "S2", "/en/b/switches/"),
    ]
    rows, unresolved, ambiguous, skipped_unresolved, skipped_ambiguous = (
        mig004._resolve_backfill(categories, products)
    )
    assert rows == {(10, 1), (11, 3)}
    assert unresolved == set()
    assert ambiguous == set()
    assert skipped_unresolved == 0
    assert skipped_ambiguous == 0
