from scraper.audit import AuditReport


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
