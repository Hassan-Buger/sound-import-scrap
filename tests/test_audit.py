from scraper.audit import AuditReport


def _row(path, level, source_count, parent=None):
    return {
        "name": path.strip("/").rsplit("/", 1)[-1].replace("-", " "),
        "slug": path.strip("/").rsplit("/", 1)[-1],
        "canonical_path": path,
        "parent_path": parent,
        "level": level,
        "source_count": source_count,
    }


def _fam(path, family):
    return {
        "id": abs(hash(path)),
        "name": path,
        "slug": path.strip("/").rsplit("/", 1)[-1],
        "canonical_path": path,
        "parent_id": None,
        "level": len(path.strip("/").split("/")),
        "product_count": family,
        "is_active": True,
    }


def _prog(path, total, source_count):
    return {
        "id": total if path else 1,
        "canonical_path": path,
        "status": "completed",
        "attempt_count": 1,
        "source_count": source_count,
        "total_products": total,
        "products_scraped": total,
        "pages_processed": 1,
        "last_error": None,
    }


def test_audit_reports_exact_tree_and_failure_differences():
    source = [
        {
            "name": "A",
            "slug": "a",
            "canonical_path": "/en/a/",
            "parent_path": None,
            "level": 1,
            "source_count": 2,
        },
        {
            "name": "Switches",
            "slug": "switches",
            "canonical_path": "/en/a/switches/",
            "parent_path": "/en/a/",
            "level": 2,
            "source_count": 2,
        },
        {
            "name": "B",
            "slug": "b",
            "canonical_path": "/en/b/",
            "parent_path": None,
            "level": 1,
            "source_count": 0,
        },
        {
            "name": "Switches",
            "slug": "switches",
            "canonical_path": "/en/b/switches/",
            "parent_path": "/en/b/",
            "level": 2,
            "source_count": 1,
        },
    ]
    database = [
        {
            "id": 1,
            "name": "A",
            "slug": "a",
            "canonical_path": "/en/a/",
            "parent_id": None,
            "level": 1,
            "product_count": 1,
            "is_active": True,
        },
        {
            "id": 2,
            "name": "Switches",
            "slug": "switches",
            "canonical_path": "/en/a/switches/",
            "parent_id": None,
            "level": 3,
            "product_count": 1,
            "is_active": True,
        },
        {
            "id": 3,
            "name": "B",
            "slug": "b",
            "canonical_path": "/en/b/",
            "parent_id": None,
            "level": 1,
            "product_count": 0,
            "is_active": True,
        },
    ]
    progress = [
        {
            "id": 10,
            "canonical_path": "/en/a/switches/",
            "status": "failed",
            "attempt_count": 3,
            "source_count": 2,
            "total_products": 2,
            "products_scraped": 1,
            "pages_processed": 1,
            "last_error": "timeout",
        }
    ]

    report = AuditReport.build(source, source, database, progress)

    assert report.missing_from_scraper == []
    assert report.missing_from_db == ["/en/b/switches/"]
    assert report.duplicate_slugs["switches"] == [
        "/en/a/switches/",
        "/en/b/switches/",
    ]
    assert report.parent_mismatches[0]["canonical_path"] == "/en/a/switches/"
    assert report.level_mismatches[0]["database_level"] == 3
    assert {row["canonical_path"] for row in report.count_differences} == {
        "/en/a/",
        "/en/a/switches/",
    }
    assert report.unscrapable[0]["last_error"] == "timeout"
    assert report.status_counts == {"failed": 1}

    rendered = report.render(verbose=True)
    assert "MISSING FROM DATABASE" in rendered
    assert "/en/b/switches/" in rendered
    assert "FAILED OR INCOMPLETE CATEGORIES" in rendered


def test_count_differences_use_catalog_when_available():
    """Completed categories compare against the scraped catalog total,
    not the sitemap counter. The empty-listing parent is classified
    separately and the source sitemap count is preserved via `basis`."""
    source_cats = [
        _row("/en/accessories/", 1, 299),
        _row("/en/accessories/cables/", 2, 310, "/en/accessories/"),
        _row("/en/accessories/cables/arc-welding/", 3, 120, "/en/accessories/cables/"),
    ]
    db_cats = [
        _fam("/en/accessories/", 0),
        _fam("/en/accessories/cables/", 5),
        _fam("/en/accessories/cables/arc-welding/", 3),
    ]
    progress = [
        _prog("/en/accessories/", 0, 299),
        _prog("/en/accessories/cables/", 5, 310),
        _prog("/en/accessories/cables/arc-welding/", 5, 120),
    ]

    report = AuditReport.build(source_cats, source_cats, db_cats, progress)

    diff_paths = [row["canonical_path"] for row in report.count_differences]
    assert diff_paths == ["/en/accessories/cables/arc-welding/"]

    row = report.count_differences[0]
    assert row["source"] == 5
    assert row["sitemap"] == 120
    assert row["catalog"] == 5
    assert row["database"] == 3
    assert row["difference"] == -2
    assert row["basis"] == "catalog"

    assert [r["canonical_path"] for r in report.empty_listing_parents] == [
        "/en/accessories/"
    ]
    empty_parent = report.empty_listing_parents[0]
    assert empty_parent["placeholder"] == 299
    assert empty_parent["total"] == 0
    assert empty_parent["children_product_count"] == 5 + 3

    rendered = report.render(verbose=True)
    assert "EMPTY-LISTING PARENT CATEGORIES" in rendered
    assert "basis=catalog" in rendered
