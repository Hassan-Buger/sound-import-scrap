import asyncio
import json
import logging
import sys
from typing import Optional

import click

from app.config import settings

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("cli")


async def _init_db():
    from app.migrations import upgrade_database

    await upgrade_database()
    logger.info("Database migrations applied")


@click.group()
def cli():
    """SoundImports Scraper - CLI tools for scraping and management."""


@cli.command()
@click.option("--full", is_flag=True, default=True, help="Full scrape (default)")
@click.option(
    "--incremental", is_flag=True, default=False, help="Incremental scrape (resume)"
)
@click.option(
    "--concurrency", type=int, default=None, help="Override product concurrency"
)
@click.option(
    "--categories",
    type=str,
    default=None,
    help="Comma-separated category slugs to scrape",
)
def scrape(
    full: bool, incremental: bool, concurrency: Optional[int], categories: Optional[str]
):
    """Run the product scraper."""
    asyncio.run(_run_scrape(full, incremental, concurrency, categories))


async def _run_scrape(
    full: bool,
    incremental: bool,
    concurrency: Optional[int],
    category_filter: Optional[str],
):
    await _init_db()

    from scraper.soundimports import SoundImportsScraper
    from scraper.pipeline import ScrapePipeline

    if concurrency:
        import importlib

        app_config = importlib.import_module("app.config")
        app_config.settings.product_concurrency = concurrency
        app_config.settings.concurrency = concurrency

    supplier = SoundImportsScraper()
    filters = {
        token.strip()
        for token in (category_filter or "").split(",")
        if token.strip()
    }
    pipeline = ScrapePipeline(
        supplier,
        full=(full and not incremental),
        category_filter=filters,
    )

    try:
        stats = await pipeline.run()
        _print_summary(stats)
    except Exception as e:
        logger.exception("Scrape failed")
        sys.exit(1)
    finally:
        await supplier._client.close()


def _print_summary(stats):
    click.echo("")
    click.echo("=" * 60)
    click.echo("SCRAPE SUMMARY")
    click.echo("=" * 60)
    click.echo(f"Job ID:               {stats.get('job_id')}")
    click.echo("")
    click.echo(f"Categories discovered: {stats.get('categories_discovered')}")
    click.echo(f"Categories completed:  {stats.get('categories_succeeded')}")
    click.echo(f"Categories failed:     {stats.get('categories_failed')}")
    click.echo(f"Categories skipped:    {stats.get('categories_skipped')}")
    click.echo("")
    click.echo(f"Products discovered:   {stats.get('products_total')}")
    click.echo(f"Products new:          {stats.get('products_new')}")
    click.echo(f"Products updated:      {stats.get('products_updated')}")
    click.echo(f"Products failed:       {stats.get('products_failed')}")
    click.echo(f"Pages fetched:         {stats.get('pages_fetched')}")
    click.echo("")
    click.echo(f"Product/category links:")
    click.echo(f"  Created:             {stats.get('relationships_created')}")
    click.echo(f"  Existing:            {stats.get('relationships_existing')}")
    click.echo(f"  Removed stale:       {stats.get('relationships_removed')}")
    click.echo(f"Category discrepancies:{stats.get('category_discrepancies')}")
    click.echo("")
    click.echo(f"HTTP requests:          {stats.get('http_requests')}")
    click.echo(f"HTTP retries:           {stats.get('http_retries')}")
    click.echo(f"HTTP failures:          {stats.get('http_failures')}")
    click.echo("")
    click.echo(f"Job status:            {stats.get('job_status')}")
    click.echo(f"Time elapsed:          {stats.get('elapsed_seconds', 0):.1f}s")
    click.echo("=" * 60)


@cli.command()
@click.option("--slug", type=str, default=None, help="Category slug to list")
def categories(slug: Optional[str]):
    """List discovered categories from the sitemap."""

    async def _run():
        await _init_db()
        from scraper.soundimports import SoundImportsScraper

        supplier = SoundImportsScraper()
        try:
            cats = await supplier.discover_categories()
            if slug:
                cats = [c for c in cats if slug in c["slug"]]
            click.echo(f"Found {len(cats)} categories:")
            for c in cats:
                prefix = "  " * c.get("level", 0)
                click.echo(f"  {prefix}- {c['name']} [{c['slug']}]")
                click.echo(f"  {prefix}  {c['url']}")
        finally:
            await supplier._client.close()

    asyncio.run(_run())


@cli.command()
@click.argument("url")
@click.option("--page", type=int, default=1)
@click.option("--limit", type=int, default=10)
def category(url: str, page: int, limit: int):
    """Fetch products from a category URL."""

    async def _run():
        await _init_db()
        from scraper.soundimports import SoundImportsScraper

        supplier = SoundImportsScraper()
        try:
            data = await supplier.get_product_list(url, page=page, limit=limit)
            products = supplier.category_scraper.extract_products(data)
            click.echo(f"Found {len(products)} products (page {page}):")
            for p in products[:limit]:
                summary = supplier.extract_product_summary(p)
                click.echo(
                    f"  SKU: {summary['sku']} | {summary.get('name', 'N/A')} | {summary.get('price', 'N/A')}"
                )
                click.echo(f"       {summary['url']}")
        finally:
            await supplier._client.close()

    asyncio.run(_run())


@cli.command()
@click.argument("url")
def product(url: str):
    """Fetch full product details from a product URL."""

    async def _run():
        await _init_db()
        from scraper.soundimports import SoundImportsScraper

        supplier = SoundImportsScraper()
        try:
            data = await supplier.get_product_detail(url)
            parsed = supplier.extract_product_detail(data)
            click.echo(f"SKU:            {parsed['sku']}")
            click.echo(f"Title:          {parsed.get('title', 'N/A')}")
            click.echo(f"Brand:          {parsed.get('brand', 'N/A')}")
            click.echo(
                f"Price:          {parsed.get('price', 'N/A')} {parsed.get('currency', 'EUR')}"
            )
            click.echo(f"Stock:          {parsed.get('stock', 'N/A')}")
            click.echo(f"EAN:            {parsed.get('ean', 'N/A')}")
            click.echo(f"Images:         {len(parsed.get('images', []))}")
            click.echo(f"Attributes:     {len(parsed.get('attributes', []))}")
            click.echo(f"URL:            {parsed.get('url', 'N/A')}")
        finally:
            await supplier._client.close()

    asyncio.run(_run())


@cli.command()
@click.option(
    "--json",
    "json_out",
    is_flag=True,
    default=False,
    help="Output machine-readable JSON",
)
@click.option(
    "--verbose", is_flag=True, default=False, help="Include per-category detail"
)
@click.option(
    "--category", default=None, help="Restrict to categories matching this path/slug"
)
@click.option(
    "--fix", is_flag=True, default=False, help="Sync source categories missing from DB"
)
def audit_categories(json_out: bool, verbose: bool, category: Optional[str], fix: bool):
    """Compare the live SoundImports category tree against the database."""

    async def _run():
        await _init_db()
        from scraper.soundimports import SoundImportsScraper
        from scraper.audit import AuditReport
        from app import crud
        from app.database import async_session_factory
        from app.models import ScrapeProgress
        from sqlalchemy import select

        supplier = SoundImportsScraper()
        try:
            all_source_cats = await supplier.discover_categories()
            source_cats = all_source_cats
            selected_paths = set()
            if category:
                selected = [
                    c
                    for c in all_source_cats
                    if category in c["canonical_path"] or category in c["slug"]
                ]
                by_path = {c["canonical_path"]: c for c in all_source_cats}
                selected_paths = {c["canonical_path"] for c in selected}
                queue = list(selected)
                while queue:
                    parent_ref = queue.pop().get("parent_path")
                    if parent_ref and parent_ref not in selected_paths:
                        parent = by_path.get(parent_ref)
                        if parent:
                            selected_paths.add(parent_ref)
                            queue.append(parent)
                source_cats = [
                    c for c in all_source_cats if c["canonical_path"] in selected_paths
                ]

            async with async_session_factory() as db:
                db_cats = await crud.get_all_categories(db)
                direct_counts = await crud.get_direct_product_counts(db)
                if category:
                    db_cats = [
                        c
                        for c in db_cats
                        if c.canonical_path in selected_paths
                        or category in (c.canonical_path or "")
                        or category in c.slug
                    ]
                report_db_rows = [
                    {
                        "id": c.id,
                        "name": c.name,
                        "slug": c.slug,
                        "canonical_path": c.canonical_path,
                        "level": c.level,
                        "parent_id": c.parent_id,
                        "product_count": c.product_count,
                        "source_product_count": c.source_product_count,
                        "is_active": c.is_active,
                        "missing_streak": c.missing_streak,
                    }
                    for c in db_cats
                ]
                progress_models = list(
                    (
                        await db.execute(
                            select(ScrapeProgress).order_by(ScrapeProgress.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                progress_rows = [
                    {
                        "id": row.id,
                        "canonical_path": row.canonical_path,
                        "status": row.status,
                        "attempt_count": row.attempt_count,
                        "source_count": row.source_count,
                        "total_products": row.total_products,
                        "products_scraped": row.products_scraped,
                        "pages_processed": row.pages_processed,
                        "last_error": row.last_error,
                    }
                    for row in progress_models
                    if not category
                    or row.canonical_path in selected_paths
                    or category in (row.canonical_path or "")
                ]

            # The live sitemap is parsed directly by the production parser;
            # therefore source_cats is also the normalized scraper view.
            report = AuditReport.build(
                source_cats,
                source_cats,
                report_db_rows,
                progress_rows,
                supplier.sitemap_parser.last_diagnostics,
                direct_counts=direct_counts,
            )

            if fix:
                async with async_session_factory() as db:
                    for cat in sorted(source_cats, key=lambda row: row.get("level", 0)):
                        await crud.upsert_category_with_parent(
                            db,
                            name=cat["name"],
                            slug=cat["slug"],
                            canonical_path=cat["canonical_path"],
                            url=cat["url"],
                            level=cat.get("level", 0),
                            parent_path_ref=cat.get("parent_path"),
                            source_count=cat.get("source_count", 0),
                        )
                    await db.commit()
                click.echo(
                    f"Reconciled {len(source_cats)} source categories into the database."
                )
                async with async_session_factory() as db:
                    refreshed = await crud.get_all_categories(db)
                    if category:
                        refreshed = [
                            c
                            for c in refreshed
                            if c.canonical_path in selected_paths
                            or category in (c.canonical_path or "")
                            or category in c.slug
                        ]
                    report_db_rows = [
                        {
                            "id": c.id,
                            "name": c.name,
                            "slug": c.slug,
                            "canonical_path": c.canonical_path,
                            "level": c.level,
                            "parent_id": c.parent_id,
                            "product_count": c.product_count,
                            "source_product_count": c.source_product_count,
                            "is_active": c.is_active,
                            "missing_streak": c.missing_streak,
                        }
                        for c in refreshed
                    ]
                report = AuditReport.build(
                    source_cats,
                    source_cats,
                    report_db_rows,
                    progress_rows,
                    supplier.sitemap_parser.last_diagnostics,
                    direct_counts=direct_counts,
                )

            if json_out:
                click.echo(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
            else:
                click.echo(report.render(verbose=verbose))
        finally:
            await supplier._client.close()

    asyncio.run(_run())


@cli.command()
@click.option("--host", default=None, help="API host")
@click.option("--port", type=int, default=None, help="API port")
def serve(host: Optional[str], port: Optional[int]):
    """Start the FastAPI server."""
    import os
    import uvicorn

    env_port = os.getenv("PORT")
    env_api_port = os.getenv("API_PORT")

    # In Railway/Heroku/Render, PORT is automatically injected by the platform.
    # Prioritize explicitly passed --port, then PORT, then API_PORT, then settings.api_port.
    if port is None:
        if env_port:
            port = int(env_port)
        elif env_api_port:
            port = int(env_api_port)
        else:
            port = settings.api_port

    host = host or os.getenv("HOST") or settings.api_host

    logger.info("Starting uvicorn server on %s:%d", host, port)
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=False,
        log_level=settings.log_level.lower(),
    )


@cli.command()
@click.option("--output", "-o", default="products.json", help="Output JSON file path")
@click.option("--brand", default=None, help="Filter by brand")
@click.option("--category", default=None, help="Filter by category slug")
@click.option("--search", default=None, help="Search in title/SKU/brand")
@click.option(
    "--sort-by", default=None, help="Sort field: sku, title, brand, price, stock"
)
@click.option("--sort-order", default="desc", help="asc or desc")
def export(
    output: str,
    brand: Optional[str],
    category: Optional[str],
    search: Optional[str],
    sort_by: Optional[str],
    sort_order: str,
):
    """Export products to a JSON file using clean API field names."""

    async def _run():
        await _init_db()
        from app.crud import export_products
        from app.database import async_session_factory
        from app.schemas import ProductDetail
        import json

        async with async_session_factory() as db:
            products = await export_products(
                db,
                brand=brand,
                category_slug=category,
                search=search,
                sort_by=sort_by,
                sort_order=sort_order,
            )

        data = [ProductDetail.model_validate(p).model_dump() for p in products]

        with open(output, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        click.echo(f"Exported {len(data)} products to {output}")

    asyncio.run(_run())


if __name__ == "__main__":
    cli()
