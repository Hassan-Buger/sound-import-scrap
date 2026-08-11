"""Exact category-tree, relationship-count, and progress reconciliation."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional


def _duplicates(rows: Iterable[Dict], field: str) -> Dict[str, List[str]]:
    grouped: Dict[str, List[str]] = defaultdict(list)
    for row in rows:
        value = row.get(field)
        path = row.get("canonical_path")
        if value and path:
            grouped[str(value)].append(str(path))
    return {
        value: sorted(paths)
        for value, paths in grouped.items()
        if len(paths) > 1
    }


class AuditReport:
    """Serializable comparison of source, parser, database, and scrape state."""

    def __init__(self) -> None:
        self.source_categories: List[Dict] = []
        self.scraper_categories: List[Dict] = []
        self.db_categories: List[Dict] = []
        self.missing_from_scraper: List[str] = []
        self.missing_from_db: List[str] = []
        self.db_only: List[str] = []
        self.duplicate_slugs: Dict[str, List[str]] = {}
        self.duplicate_paths: List[str] = []
        self.duplicate_names: Dict[str, List[str]] = {}
        self.orphans: List[Dict] = []
        self.parent_mismatches: List[Dict] = []
        self.level_mismatches: List[Dict] = []
        self.count_differences: List[Dict] = []
        self.empty_listing_parents: List[Dict] = []
        self.unscrapable: List[Dict] = []
        self.status_counts: Dict[str, int] = {}
        self.parser_diagnostics: Dict = {}

    @classmethod
    def build(
        cls,
        source: List[Dict],
        scraper: List[Dict],
        database: List[Dict],
        progress: Optional[List[Dict]] = None,
        parser_diagnostics: Optional[Dict] = None,
        direct_counts: Optional[Dict[str, int]] = None,
    ) -> "AuditReport":
        report = cls()
        report.source_categories = list(source)
        report.scraper_categories = list(scraper)
        report.db_categories = list(database)
        report.parser_diagnostics = dict(parser_diagnostics or {})

        source_paths = {
            row.get("canonical_path") for row in source if row.get("canonical_path")
        }
        scraper_paths = {
            row.get("canonical_path") for row in scraper if row.get("canonical_path")
        }
        db_paths = {
            row.get("canonical_path") for row in database if row.get("canonical_path")
        }
        report.missing_from_scraper = sorted(source_paths - scraper_paths)
        report.missing_from_db = sorted(source_paths - db_paths)
        report.db_only = sorted(db_paths - source_paths)

        path_counts = Counter(
            row.get("canonical_path")
            for row in database
            if row.get("canonical_path")
        )
        report.duplicate_paths = sorted(
            path for path, count in path_counts.items() if count > 1
        )
        report.duplicate_slugs = _duplicates(source or database, "slug")
        report.duplicate_names = _duplicates(source or database, "name")

        source_by_path = {
            row["canonical_path"]: row
            for row in source
            if row.get("canonical_path")
        }
        db_by_path = {
            row["canonical_path"]: row
            for row in database
            if row.get("canonical_path")
        }
        db_by_id = {row.get("id"): row for row in database if row.get("id") is not None}

        all_db_paths = {
            row.get("canonical_path")
            for row in database
            if row.get("canonical_path")
        }
        direct = dict(direct_counts or {})
        latest_progress: Dict[str, Dict] = {}
        for row in progress or []:
            path = row.get("canonical_path")
            if not path:
                continue
            current = latest_progress.get(path)
            if current is None or (row.get("id") or 0) > (current.get("id") or 0):
                latest_progress[path] = row

        for db_row in database:
            path = db_row.get("canonical_path")
            parent_id = db_row.get("parent_id")
            source_row = source_by_path.get(path)
            if parent_id is not None and parent_id not in db_by_id:
                report.orphans.append(
                    {
                        **db_row,
                        "reason": f"parent_id {parent_id} does not exist",
                    }
                )
            if source_row is None:
                continue

            expected_parent_path = source_row.get("parent_path")
            expected_parent = db_by_path.get(expected_parent_path)
            expected_parent_id = (
                expected_parent.get("id") if expected_parent is not None else None
            )
            if expected_parent_path and expected_parent is None:
                report.orphans.append(
                    {
                        **db_row,
                        "reason": f"source parent {expected_parent_path} is missing from DB",
                        "expected_parent_path": expected_parent_path,
                    }
                )
            elif parent_id != expected_parent_id:
                report.parent_mismatches.append(
                    {
                        **db_row,
                        "expected_parent_path": expected_parent_path,
                        "expected_parent_id": expected_parent_id,
                        "actual_parent_id": parent_id,
                    }
                )

            source_level = source_row.get("level")
            if source_level is not None and db_row.get("level") != source_level:
                report.level_mismatches.append(
                    {
                        "canonical_path": path,
                        "source_level": source_level,
                        "database_level": db_row.get("level"),
                    }
                )

            # Count reconciliation.  Two different numbers exist on the site:
            #   * sitemap counter  (source_row.source_count): a family/aggregate
            #     metric that frequently disagrees with the real catalog.
            #   * JSON catalog count (progress.total_products): the number of
            #     products actually enumerable on the category URL; this is the
            #     value the scraper verifies (list_total == unique listed).
            # A category whose own listing is empty but which has children
            # (e.g. /accessories/cables/) is NOT a discrepancy: its products
            # live in the children.  Such rows are listed separately.
            family_count = db_row.get("product_count") or 0
            src_count = source_row.get("source_count") or 0
            prog_row = latest_progress.get(path)
            catalog_count = None
            if prog_row is not None:
                catalog_count = prog_row.get("total_products")
            has_children = any(
                other != path
                and other is not None
                and other.startswith(path.rstrip("/") + "/")
                for other in all_db_paths
            )
            if catalog_count == 0 and has_children:
                child_paths = [
                    other
                    for other in all_db_paths
                    if other and other != path and other.startswith(path.rstrip("/") + "/")
                ]
                report.empty_listing_parents.append(
                    {
                        "canonical_path": path,
                        "placeholder": src_count,
                        "total": 0,
                        "children_product_count": sum(
                            (db_by_path.get(child) or {}).get("product_count")
                            or (latest_progress.get(child) or {}).get("total_products")
                            or 0
                            for child in child_paths
                        ),
                    }
                )
                continue
            if catalog_count:
                if catalog_count != family_count:
                    report.flag_count_difference(
                        path,
                        catalog_count,
                        family_count,
                        basis="catalog",
                        sitemap=src_count,
                        catalog=catalog_count,
                        family=family_count,
                        direct=direct.get(path, 0),
                    )
            elif src_count and src_count != family_count:
                report.flag_count_difference(
                    path,
                    src_count,
                    family_count,
                    basis="sitemap",
                    sitemap=src_count,
                    catalog=None,
                    family=family_count,
                    direct=direct.get(path, 0),
                )

        report.status_counts = dict(
            Counter((row.get("status") or "unknown").lower() for row in latest_progress.values())
        )
        for path, row in sorted(latest_progress.items()):
            status = (row.get("status") or "").lower()
            discovered = row.get("total_products") or 0
            scraped = row.get("products_scraped") or 0
            if status in {"failed", "retrying"} or (
                status == "completed" and discovered and scraped < discovered
            ):
                report.unscrapable.append(
                    {
                        "canonical_path": path,
                        "status": status,
                        "attempt_count": row.get("attempt_count") or 0,
                        "source_count": row.get("source_count") or 0,
                        "discovered_products": discovered,
                        "products_scraped": scraped,
                        "pages_processed": row.get("pages_processed") or 0,
                        "last_error": row.get("last_error"),
                    }
                )

        return report

    @staticmethod
    def from_paths(source: List[str], db: List[Dict]) -> "AuditReport":
        source_rows = [
            {"canonical_path": path, "slug": path.strip("/").rsplit("/", 1)[-1]}
            for path in source
        ]
        return AuditReport.build(source_rows, source_rows, db)

    def compare_scraper_db(self) -> None:
        rebuilt = AuditReport.build(
            self.source_categories,
            self.scraper_categories,
            self.db_categories,
        )
        self.__dict__.update(rebuilt.__dict__)

    def flag_count_difference(
        self,
        canonical_path: str,
        source: int,
        database: int,
        *,
        basis: str = "sitemap",
        sitemap: Optional[int] = None,
        catalog: Optional[int] = None,
        family: Optional[int] = None,
        direct: Optional[int] = None,
    ) -> None:
        self.count_differences.append(
            {
                "canonical_path": canonical_path,
                "source": source,
                "sitemap": sitemap,
                "catalog": catalog,
                "database": database,
                "difference": database - source,
                "basis": basis,
                "family_db": family if family is not None else database,
                "direct_db": direct if direct is not None else database,
            }
        )

    def flag_unscrapable(self, category: Dict) -> None:
        self.unscrapable.append(category)

    def to_dict(self) -> Dict:
        return {
            "source_categories_count": len(self.source_categories),
            "scraper_categories_count": len(self.scraper_categories),
            "db_categories_count": len(self.db_categories),
            "missing_from_scraper": self.missing_from_scraper,
            "missing_from_db": self.missing_from_db,
            "db_only": self.db_only,
            "duplicate_slugs": self.duplicate_slugs,
            "duplicate_paths": self.duplicate_paths,
            "duplicate_names": self.duplicate_names,
            "orphans": self.orphans,
            "parent_mismatches": self.parent_mismatches,
            "level_mismatches": self.level_mismatches,
            "count_differences": self.count_differences,
            "empty_listing_parents": self.empty_listing_parents,
            "unscrapable": self.unscrapable,
            "status_counts": dict(self.status_counts),
            "parser_diagnostics": dict(self.parser_diagnostics),
        }

    def render(self, verbose: bool = False) -> str:
        lines: List[str] = []
        banner = "=" * 67
        source_levels = Counter(
            row.get("level", 0) for row in self.source_categories
        )
        active = sum(1 for row in self.db_categories if row.get("is_active", True))

        lines.extend(
            [
                banner,
                "SOUNDIMPORTS CATEGORY AUDIT",
                banner,
                "",
                "SOURCE:",
                f"  Raw category nodes:           {self.parser_diagnostics.get('raw_nodes', len(self.source_categories))}",
                f"  Total categories discovered: {len(self.source_categories)}",
                f"  Root categories:              {source_levels.get(1, 0)}",
                f"  Maximum hierarchy depth:      {max(source_levels, default=0)}",
                "SCRAPER:",
                f"  Total normalized:             {len(self.scraper_categories)}",
                f"  Missing from normalization:   {len(self.missing_from_scraper)}",
                f"  Duplicate paths skipped:      {len(self.parser_diagnostics.get('duplicate_paths', []))}",
                "DATABASE:",
                f"  Total categories:             {len(self.db_categories)}",
                f"  Active categories:            {active}",
                f"  Orphan categories:            {len(self.orphans)}",
                "PROGRESS:",
                f"  States:                       {self.status_counts or {}}",
                "",
            ]
        )

        sections = [
            ("MISSING FROM SCRAPER", self.missing_from_scraper, lambda row: str(row)),
            ("MISSING FROM DATABASE", self.missing_from_db, lambda row: str(row)),
            ("IN DATABASE BUT NOT ON SOURCE", self.db_only, lambda row: str(row)),
            ("DUPLICATE CANONICAL PATHS", self.duplicate_paths, lambda row: str(row)),
        ]
        for title, rows, formatter in sections:
            if rows:
                lines.extend(["-" * 67, title, "-" * 67])
                lines.extend(f"  {formatter(row)}" for row in rows)
                lines.append("")

        for title, grouped in (
            ("DUPLICATE SLUGS", self.duplicate_slugs),
            ("DUPLICATE NAMES", self.duplicate_names),
        ):
            if grouped:
                lines.extend(["-" * 67, title, "-" * 67])
                for value, paths in sorted(grouped.items()):
                    lines.append(f'  {value!r}')
                    lines.extend(f"    {path}" for path in paths)
                lines.append("")

        if self.orphans:
            lines.extend(["-" * 67, "ORPHAN CATEGORIES", "-" * 67])
            for row in self.orphans:
                lines.append(f"  {row.get('canonical_path')}: {row.get('reason')}")
            lines.append("")

        if self.parent_mismatches:
            lines.extend(["-" * 67, "PARENT/CHILD PROBLEMS", "-" * 67])
            for row in self.parent_mismatches:
                lines.append(
                    f"  {row.get('canonical_path')}: expected "
                    f"{row.get('expected_parent_path')} (id={row.get('expected_parent_id')}), "
                    f"actual id={row.get('actual_parent_id')}"
                )
            lines.append("")

        if self.level_mismatches:
            lines.extend(["-" * 67, "HIERARCHY DEPTH PROBLEMS", "-" * 67])
            for row in self.level_mismatches:
                lines.append(
                    f"  {row['canonical_path']}: source={row['source_level']} "
                    f"db={row['database_level']}"
                )
            lines.append("")

        if self.count_differences:
            lines.extend(["-" * 67, "CATEGORY COUNT DIFFERENCES", "-" * 67])
            for row in self.count_differences:
                lines.append(
                    f"  {row['canonical_path']}: source={row['source']} "
                    f"sitemap={row.get('sitemap')} catalog={row.get('catalog')} "
                    f"family={row.get('family_db')} direct={row.get('direct_db')} "
                    f"diff={row['difference']:+d} [basis={row['basis']}]"
                )
            lines.append("")

        if self.empty_listing_parents:
            lines.extend(
                ["-" * 67, "EMPTY-LISTING PARENT CATEGORIES", "-" * 67]
            )
            lines.append(
                "  Parent whose own URL lists no direct products; they live in children."
            )
            for row in self.empty_listing_parents:
                lines.append(
                    f"  {row['canonical_path']}: sitemap_placeholder={row['placeholder']} "
                    f"listed=0 children_sum={row['children_product_count']}"
                )
            for row in self.empty_listing_parents:
                child_rows = [
                    o
                    for o in sorted(self.db_categories, key=lambda x: x.get("canonical_path", ""))
                    if o.get("canonical_path", "") != row["canonical_path"]
                    and (o.get("canonical_path", "") or "").startswith(
                        row["canonical_path"].rstrip("/") + "/"
                    )
                ]
                for child in child_rows:
                    lines.append(
                        f"      child {child.get('canonical_path')}: "
                        f"family={child.get('product_count')} "
                        f"source={child.get('source_product_count')}"
                    )
            lines.append("")

        if self.unscrapable:
            lines.extend(["-" * 67, "FAILED OR INCOMPLETE CATEGORIES", "-" * 67])
            for row in self.unscrapable:
                lines.append(
                    f"  {row['canonical_path']}: status={row['status']} "
                    f"attempts={row['attempt_count']} listed={row['discovered_products']} "
                    f"scraped={row['products_scraped']} error={row.get('last_error') or '-'}"
                )
            lines.append("")

        if verbose:
            lines.extend(["-" * 67, "NORMALIZED SOURCE TREE", "-" * 67])
            for row in sorted(
                self.source_categories,
                key=lambda item: (item.get("level", 0), item.get("canonical_path", "")),
            ):
                lines.append(
                    f"  {'  ' * max(0, row.get('level', 1) - 1)}"
                    f"{row.get('canonical_path')} (source={row.get('source_count', 0)})"
                )
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"
