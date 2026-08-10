"""Category identity + product/category many-to-many migration.

Revision ID: 004
Revises: 003
Create Date: 2026-08-10

Changes
-------
categories
    * add ``canonical_path`` (unique category identity), ``is_active``,
      ``source_product_count`` and per-category scrape-state columns
    * backfill ``canonical_path`` from the existing ``url`` column
products
    * relationships are moved from the legacy comma-separated ``category_ids``
      column into the new ``product_categories`` many-to-many table
scrape_jobs / scrape_progress
    * extended with per-category lifecycle state and job-level aggregates

Data preservation
-----------------
No table is dropped. The legacy ``products.category_ids`` column is retained.
Existing category/product rows are migrated in place. Any category reference
that cannot be resolved to exactly one category is skipped and counted.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REPORT_PATH = "migration_reports/004_category_backfill.txt"


def _normalize_path(url: str) -> str:
    import re
    import unicodedata
    from urllib.parse import unquote, urlsplit

    if not url:
        return ""
    raw = url.strip()
    if not raw:
        return ""
    split = urlsplit(raw)
    path = split.path
    path = re.sub(r";[^/]+", "", path)
    path = path.replace("\\", "/")
    path = re.sub(r"/{2,}", "/", path)
    raw_segments = [seg for seg in path.split("/") if seg and seg not in (".", "..")]
    if not raw_segments:
        return "/"
    cleaned = []
    for seg in raw_segments:
        seg = unquote(seg)
        seg = unicodedata.normalize("NFKD", seg)
        seg = seg.encode("ascii", "ignore").decode("ascii").lower()
        seg = re.sub(r"[^a-z0-9\-_~.]", "-", seg)
        seg = re.sub(r"-{2,}", "-", seg)
        seg = seg.strip("-")
        if seg:
            cleaned.append(seg)
    if not cleaned:
        return "/"
    return "/" + "/".join(cleaned) + "/"


def _resolve_backfill(category_rows, product_rows):
    """Decide which legacy product/category references can be backfilled.

    Parameters
    ----------
    category_rows : iterable of (id, slug, is_active[, canonical_path])
    product_rows  : iterable of (id, sku, category_ids)

    Returns
    -------
    (rows_to_insert, unresolved_refs, ambiguous_refs, skipped_unresolved, skipped_ambiguous)
      rows_to_insert         : set of (product_id, category_id) pairs
      unresolved_refs        : set of refs with no matching category slug
      ambiguous_refs         : set of refs whose slug matches multiple categories
      skipped_unresolved     : number of references skipped (ambiguous row refs)
      skipped_ambiguous      : number of references skipped (ambiguous slug)
    """
    slug_to = {}
    id_to = {}
    path_to = {}
    for row in category_rows:
        cid, slug, active = row[:3]
        canonical_path = row[3] if len(row) > 3 else None
        slug_to.setdefault((slug or "").lower(), []).append(cid)
        id_to[str(cid)] = cid
        if canonical_path:
            path_to[str(canonical_path).lower()] = cid

    inserted_rows = set()
    unresolved_refs = set()
    ambiguous_refs = set()
    skipped_unresolved = 0
    skipped_ambiguous = 0

    for prod_id, sku, category_ids in product_rows:
        if not category_ids:
            continue
        refs = [str(r).strip() for r in str(category_ids).split(",") if str(r).strip()]
        for ref in refs:
            if ref in id_to:
                inserted_rows.add((prod_id, id_to[ref]))
                continue
            if ref.lower() in path_to:
                inserted_rows.add((prod_id, path_to[ref.lower()]))
                continue
            matches = slug_to.get(ref.lower(), [])
            if not matches:
                skipped_unresolved += 1
                unresolved_refs.add(ref)
                continue
            if len(matches) > 1:
                skipped_ambiguous += 1
                ambiguous_refs.add(ref)
                continue
            inserted_rows.add((prod_id, matches[0]))

    return (
        inserted_rows,
        unresolved_refs,
        ambiguous_refs,
        skipped_unresolved,
        skipped_ambiguous,
    )


def _write_report(lines) -> None:
    import os

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------ categories columns
    op.add_column(
        "categories", sa.Column("canonical_path", sa.String(2000), nullable=True)
    )
    op.add_column(
        "categories",
        sa.Column(
            "source_product_count", sa.Integer(), server_default="0", nullable=False
        ),
    )
    op.add_column(
        "categories",
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
    )
    op.add_column(
        "categories", sa.Column("last_scraped_at", sa.DateTime(), nullable=True)
    )
    op.add_column(
        "categories",
        sa.Column(
            "scrape_status", sa.String(50), server_default="pending", nullable=False
        ),
    )
    op.add_column(
        "categories",
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("categories", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column(
        "categories", sa.Column("source_checked_at", sa.DateTime(), nullable=True)
    )
    op.add_column(
        "categories",
        sa.Column("missing_streak", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_index("idx_categories_parent", "categories", ["parent_id"])
    op.create_index("idx_categories_active", "categories", ["is_active"])

    # Backfill canonical_path (must precede the unique index).
    rows = bind.execute(
        sa.text("SELECT id, url FROM categories ORDER BY id")
    ).fetchall()

    seen: dict = {}
    n_updates = 0
    n_dupes = 0
    for cat_id, url in rows:
        path = _normalize_path(url or "")
        if not path:
            path = f"/en/legacy-{cat_id}/"
        if path in seen:
            n_dupes += 1
            path = f"/en/__conflict-{cat_id}/".lower()
            bind.execute(
                sa.text(
                    "UPDATE categories SET canonical_path = :p, is_active = false WHERE id = :id"
                ),
                {"p": path, "id": cat_id},
            )
            continue
        seen[path] = cat_id
        bind.execute(
            sa.text("UPDATE categories SET canonical_path = :p WHERE id = :id"),
            {"p": path, "id": cat_id},
        )
        n_updates += 1

    if n_dupes:
        print(
            f"category backfill: {n_dupes} rows with duplicate canonical paths deactivated"
        )

    # Match the ORM's NOT NULL identity invariant after every row is backfilled.
    with op.batch_alter_table("categories") as batch_op:
        batch_op.alter_column(
            "canonical_path",
            existing_type=sa.String(2000),
            nullable=False,
        )

    # Unique index on canonical_path (portable across SQLite and PostgreSQL).
    op.create_index(
        "uq_categories_canonical_path", "categories", ["canonical_path"], unique=True
    )

    # ------------------------------------------------ product_categories
    op.create_table(
        "product_categories",
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("product_id", "category_id"),
    )
    op.create_index(
        "idx_product_categories_category", "product_categories", ["category_id"]
    )
    op.create_index(
        "idx_product_categories_product", "product_categories", ["product_id"]
    )

    # ------------------------------------------------ backfill from category_ids
    # Map slug -> category rows (categories may share a slug across branches).
    category_rows = bind.execute(
        sa.text("SELECT id, slug, is_active, canonical_path FROM categories")
    ).fetchall()

    product_rows = bind.execute(
        sa.text(
            "SELECT id, sku, category_ids FROM products WHERE category_ids IS NOT NULL AND category_ids <> ''"
        )
    ).fetchall()

    (
        rows_to_insert,
        unresolved_refs,
        ambiguous_refs,
        skipped_unresolved,
        skipped_ambiguous,
    ) = _resolve_backfill(category_rows, product_rows)
    inserted = len(rows_to_insert)

    params = [{"p": prod_id, "c": cat_id} for prod_id, cat_id in rows_to_insert]
    if params:
        # Executemany via a single execute() with multiple parameter sets
        # (SQLAlchemy 2.x Connection API; works for both SQLite and Postgres).
        bind.execute(
            sa.text(
                "INSERT INTO product_categories (product_id, category_id, created_at) "
                "VALUES (:p, :c, CURRENT_TIMESTAMP)"
            ),
            params,
        )
    inserted = len(params)

    # ------------------------------------------------ scrape job columns
    op.add_column("scrape_jobs", sa.Column("job_status", sa.String(50), nullable=True))
    op.add_column("scrape_jobs", sa.Column("active_key", sa.String(50), nullable=True))
    op.create_index(
        "uq_scrape_jobs_active_key",
        "scrape_jobs",
        ["active_key"],
        unique=True,
    )
    op.add_column("scrape_jobs", sa.Column("heartbeat_at", sa.DateTime(), nullable=True))
    op.add_column(
        "scrape_jobs",
        sa.Column(
            "categories_succeeded", sa.Integer(), server_default="0", nullable=False
        ),
    )
    op.add_column(
        "scrape_jobs",
        sa.Column(
            "categories_failed", sa.Integer(), server_default="0", nullable=False
        ),
    )
    op.add_column(
        "scrape_jobs",
        sa.Column(
            "categories_skipped", sa.Integer(), server_default="0", nullable=False
        ),
    )
    op.add_column(
        "scrape_jobs",
        sa.Column(
            "relationships_created", sa.Integer(), server_default="0", nullable=False
        ),
    )
    op.add_column(
        "scrape_jobs",
        sa.Column(
            "relationships_existing", sa.Integer(), server_default="0", nullable=False
        ),
    )
    op.add_column(
        "scrape_jobs",
        sa.Column(
            "category_discrepancies", sa.Integer(), server_default="0", nullable=False
        ),
    )
    op.add_column("scrape_jobs", sa.Column("summary", sa.Text(), nullable=True))

    # ------------------------------------------------ scrape progress columns
    with op.batch_alter_table("scrape_progress") as batch_op:
        batch_op.add_column(sa.Column("category_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_scrape_progress_category_id_categories",
            "categories",
            ["category_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.add_column(
        "scrape_progress", sa.Column("canonical_path", sa.String(2000), nullable=True)
    )
    op.add_column(
        "scrape_progress",
        sa.Column("status", sa.String(50), server_default="discovered", nullable=False),
    )
    op.add_column(
        "scrape_progress",
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "scrape_progress", sa.Column("started_at", sa.DateTime(), nullable=True)
    )
    op.add_column(
        "scrape_progress", sa.Column("completed_at", sa.DateTime(), nullable=True)
    )
    op.add_column("scrape_progress", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column(
        "scrape_progress", sa.Column("last_error_class", sa.String(200), nullable=True)
    )
    op.add_column(
        "scrape_progress",
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True
        ),
    )
    op.add_column(
        "scrape_progress",
        sa.Column("products_scraped", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "scrape_progress",
        sa.Column("source_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "scrape_progress",
        sa.Column("pages_processed", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_index("idx_scrape_progress_job", "scrape_progress", ["job_id"])
    op.create_index("idx_scrape_progress_cat", "scrape_progress", ["category_id"])
    op.create_index(
        "uq_scrape_progress_job_path",
        "scrape_progress",
        ["job_id", "canonical_path"],
        unique=True,
    )

    # ------------------------------------------------ report
    lines = [
        "=== Category/product backfill report (migration 004) ===",
        f"Products processed:        {len(product_rows)}",
        f"Relationships created:     {inserted}",
        f"Relationships skipped:     {skipped_unresolved + skipped_ambiguous}",
        f"Unresolved categories:     {skipped_unresolved}",
        f"Ambiguous categories:      {skipped_ambiguous}",
        f"Category rows backfilled:  {n_updates}",
        "",
        "Unresolved category refs:",
    ]
    lines.extend(sorted(unresolved_refs))
    lines.append("")
    lines.append("Ambiguous category refs (slug matched >1 category):")
    lines.extend(sorted(ambiguous_refs))
    try:
        _write_report(lines)
    except OSError as exc:
        # Schema/data migration must not fail merely because a Railway image
        # has a read-only working directory. The report is diagnostic output.
        print(f"warning: could not write migration report {REPORT_PATH}: {exc}")


def downgrade() -> None:
    op.drop_index("uq_scrape_progress_job_path", table_name="scrape_progress")
    op.drop_index("idx_scrape_progress_cat", table_name="scrape_progress")
    op.drop_index("idx_scrape_progress_job", table_name="scrape_progress")
    op.drop_column("scrape_progress", "created_at")
    op.drop_column("scrape_progress", "pages_processed")
    op.drop_column("scrape_progress", "source_count")
    op.drop_column("scrape_progress", "products_scraped")
    op.drop_column("scrape_progress", "last_error_class")
    op.drop_column("scrape_progress", "last_error")
    op.drop_column("scrape_progress", "completed_at")
    op.drop_column("scrape_progress", "started_at")
    op.drop_column("scrape_progress", "attempt_count")
    op.drop_column("scrape_progress", "status")
    op.drop_column("scrape_progress", "canonical_path")
    with op.batch_alter_table("scrape_progress") as batch_op:
        batch_op.drop_constraint(
            "fk_scrape_progress_category_id_categories", type_="foreignkey"
        )
        batch_op.drop_column("category_id")

    op.drop_column("scrape_jobs", "summary")
    op.drop_column("scrape_jobs", "category_discrepancies")
    op.drop_column("scrape_jobs", "relationships_existing")
    op.drop_column("scrape_jobs", "relationships_created")
    op.drop_column("scrape_jobs", "categories_skipped")
    op.drop_column("scrape_jobs", "categories_failed")
    op.drop_column("scrape_jobs", "categories_succeeded")
    op.drop_index("uq_scrape_jobs_active_key", table_name="scrape_jobs")
    op.drop_column("scrape_jobs", "active_key")
    op.drop_column("scrape_jobs", "job_status")
    op.drop_column("scrape_jobs", "heartbeat_at")

    op.drop_index("idx_product_categories_product", table_name="product_categories")
    op.drop_index("idx_product_categories_category", table_name="product_categories")
    op.drop_table("product_categories")

    op.drop_index("uq_categories_canonical_path", table_name="categories")
    op.drop_index("idx_categories_active", table_name="categories")
    op.drop_index("idx_categories_parent", table_name="categories")
    op.drop_column("categories", "missing_streak")
    op.drop_column("categories", "source_checked_at")
    op.drop_column("categories", "last_error")
    op.drop_column("categories", "attempt_count")
    op.drop_column("categories", "scrape_status")
    op.drop_column("categories", "last_scraped_at")
    op.drop_column("categories", "is_active")
    op.drop_column("categories", "source_product_count")
    op.drop_column("categories", "canonical_path")
