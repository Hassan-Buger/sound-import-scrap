import asyncio
import json
import logging
import sys
from typing import Optional

import click

from app.config import settings
from app.database import engine, Base

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("cli")


async def _init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ensured")


@click.group()
def cli():
    """SoundImports Scraper - CLI tools for scraping and management."""


@cli.command()
@click.option("--full", is_flag=True, default=True, help="Full scrape (default)")
@click.option("--incremental", is_flag=True, default=False, help="Incremental scrape (resume)")
@click.option("--concurrency", type=int, default=None, help="Override concurrency")
@click.option("--categories", type=str, default=None, help="Comma-separated category slugs to scrape")
def scrape(full: bool, incremental: bool, concurrency: Optional[int], categories: Optional[str]):
    """Run the product scraper."""
    asyncio.run(_run_scrape(full, incremental, concurrency, categories))


async def _run_scrape(full: bool, incremental: bool, concurrency: Optional[int], category_filter: Optional[str]):
    await _init_db()

    from scraper.soundimports import SoundImportsScraper
    from scraper.pipeline import ScrapePipeline

    if concurrency:
        import importlib
        app_config = importlib.import_module("app.config")
        app_config.settings.concurrency = concurrency

    supplier = SoundImportsScraper()
    pipeline = ScrapePipeline(supplier, full=(full and not incremental))

    try:
        stats = await pipeline.run()
        click.echo("\nScrape completed successfully!")
        click.echo(f"  Categories discovered: {stats['categories_discovered']}")
        click.echo(f"  Categories completed:  {stats['categories_completed']}")
        click.echo(f"  Products total:        {stats['products_total']}")
        click.echo(f"  Products new:          {stats['products_new']}")
        click.echo(f"  Products updated:      {stats['products_updated']}")
        click.echo(f"  Products failed:       {stats['products_failed']}")
        click.echo(f"  Pages fetched:         {stats['pages_fetched']}")
        click.echo(f"  Time elapsed:          {stats.get('elapsed_seconds', 0):.1f}s")
    except Exception as e:
        logger.exception("Scrape failed")
        sys.exit(1)
    finally:
        await supplier._client.close()


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
                click.echo(f"  SKU: {summary['sku']} | {summary.get('name', 'N/A')} | {summary.get('price', 'N/A')}")
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
            click.echo(f"Price:          {parsed.get('price', 'N/A')} {parsed.get('currency', 'EUR')}")
            click.echo(f"Stock:          {parsed.get('stock', 'N/A')}")
            click.echo(f"EAN:            {parsed.get('ean', 'N/A')}")
            click.echo(f"Images:         {len(parsed.get('images', []))}")
            click.echo(f"Attributes:     {len(parsed.get('attributes', []))}")
            click.echo(f"URL:            {parsed.get('url', 'N/A')}")
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
@click.option("--sort-by", default=None, help="Sort field: sku, title, brand, price, stock")
@click.option("--sort-order", default="desc", help="asc or desc")
def export(output: str, brand: Optional[str], category: Optional[str],
           search: Optional[str], sort_by: Optional[str], sort_order: str):
    """Export products to a JSON file using clean API field names."""
    async def _run():
        await _init_db()
        from app.crud import export_products
        from app.database import async_session_factory
        from app.schemas import ProductDetail
        import json

        async with async_session_factory() as db:
            products = await export_products(
                db, brand=brand, category_slug=category, search=search,
                sort_by=sort_by, sort_order=sort_order,
            )

        data = [ProductDetail.model_validate(p).model_dump() for p in products]

        with open(output, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        click.echo(f"Exported {len(data)} products to {output}")

    asyncio.run(_run())


if __name__ == "__main__":
    cli()
