# SoundImports Scraper — REST API & Data Pipeline

**Automated e-commerce data scraper** for [SoundImports.eu](https://www.soundimports.eu/) with a normalized REST API, PostgreSQL/SQLite storage, JSON export, and WordPress plugin integration.

**Design Philosophy:** The REST API is the **stable contract** between the scraper and any frontend (WordPress, mobile app, etc.). If the supplier changes their API, only the Python backend changes — the plugin stays untouched.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Quick Start — Two Ways](#quick-start--two-ways)
3. [Scraping Data](#scraping-data)
4. [API Reference — All 8 Endpoints](#api-reference--all-8-endpoints)
5. [Testing the API](#testing-the-api)
6. [Downloading Data (JSON Export)](#downloading-data-json-export)
7. [WordPress Plugin Integration](#wordpress-plugin-integration)
8. [Project Structure](#project-structure)
9. [Configuration](#configuration)
10. [Database Schema](#database-schema)

---

## Architecture Overview

```
┌─────────────┐     HTTP/JSON       ┌──────────────┐     SQL     ┌────────────┐
│ SoundImports│  ◄──────────────►   │  Scraper CLI │  ◄────────► │ PostgreSQL │
│   .eu API   │    (async aiohttp)  │  (Python)    │             │  SQLite    │
└─────────────┘                     │              │             └────────────┘
                                    │  Pipeline    │
                                    │  ↓           │             ┌────────────┐
                                    │  FastAPI     │  ◄────────► │  WordPress │
                                    │  REST API    │    JSON     │   Plugin   │
                                    └──────────────┘             └────────────┘
```

### Key Technical Decisions

| Decision | Rationale |
|----------|-----------|
| **JSON API only** | No HTML scraping. Uses SoundImports' official JSON endpoints. Fast (~6 min for 4,200 products) and reliable. |
| **Full async** | `aiohttp` + `asyncio` with 50 concurrent requests. Retry with exponential backoff. |
| **Dual database** | PostgreSQL for production (via Docker), SQLite for local development (zero setup). |
| **Pydantic normalization** | All API responses go through strict Pydantic v2 models. The API never exposes raw supplier JSON. |
| **Resume support** | If the scraper stops mid-run, `--incremental` mode continues where it left off. |
| **Idempotent upserts** | Products are keyed by unique SKU. Running the scraper multiple times updates existing records. |
| **JSON file export** | After each scrape, the pipeline auto-generates `categories.json`, `brands.json`, and per-product JSON files for offline debugging. |

### What Gets Scraped

- **172 categories** in a 3-level hierarchy (e.g., `Home Audio > Speakers > Bookshelf Speakers`)
- **~4,200 unique products** across all categories (each product may appear in multiple categories)
- **Per product:** SKU, EAN, title, short/long description, price, stock, brand, images, technical attributes, categories

---

## Quick Start — Two Ways

### Option A: Docker (PostgreSQL — Production)

**Prerequisites:** Docker Desktop, Git

```bash
# 1. Clone the project
git clone <repo-url> soundimports-scraper
cd soundimports-scraper

# 2. Start PostgreSQL + API
docker compose up -d db api

# 3. Run full scrape (~6 minutes, 4,200 products)
docker compose run --rm scraper scrape

# 4. Open Swagger UI
#    http://localhost:8000/docs
```

### Option B: Local (SQLite — No Docker, for Development)

**Prerequisites:** Python 3.11+, Git

```powershell
# Windows PowerShell

# 1. Clone the project
git clone <repo-url> soundimports-scraper
cd soundimports-scraper

# 2. Create virtual environment & install
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 3. Set SQLite database (no PostgreSQL needed)
$env:DATABASE_URL = "sqlite+aiosqlite:///./soundimports.db"

# 4. Start the API server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 5. In a SECOND terminal, scrape data
$env:DATABASE_URL = "sqlite+aiosqlite:///./soundimports.db"
python -m scraper.cli scrape

# 6. Open Swagger UI
#    http://localhost:8000/docs
```

```bash
# Linux/macOS
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL="sqlite+aiosqlite:///./soundimports.db"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 &
python -m scraper.cli scrape
```

---

## Scraping Data

The scraper follows this pipeline:

```
Sitemap HTML → 172 Categories → Each Category (paginated) → Product Detail (JSON) → Database
```

### Scraper Commands

```bash
# Full scrape (all categories, all products)
docker compose run --rm scraper scrape

# Incremental (resume if previous run was interrupted)
docker compose run --rm scraper scrape --incremental

# Override concurrency (default 50)
docker compose run --rm scraper scrape --concurrency 30

# List discovered categories from sitemap (without scraping)
docker compose run --rm scraper categories

# Test a single category page
docker compose run --rm scraper category "https://www.soundimports.eu/en/home-audio/speakers/" --limit 5

# Test a single product detail
docker compose run --rm scraper product "https://www.soundimports.eu/en/hivi-os-10.html"
```

**Scraper Output (Terminal):**

```
2026-07-09 14:46:10 [INFO] scraper.pipeline: Discovered 172 categories
2026-07-09 14:46:11 [INFO] scraper.pipeline: Category started: Woofers (...)
...
2026-07-09 14:52:25 [INFO] scraper.pipeline: Scrape finished in 377.4s:
  172 categories, 14619 products (14619 new, 0 updated, 0 failed)

Scrape completed successfully!
  Categories discovered: 172
  Categories completed:  172
  Products total:        14619
  Products new:          14619
  Products updated:      0
  Products failed:       0
  Pages fetched:         265
  Time elapsed:          377.4s
```

> **Note:** The "14,619 products total" counts each product-category relationship. Unique SKUs = ~4,206 (each product appears in ~3.5 categories on average).

---

## API Reference — All 8 Endpoints

Base URL: **`http://localhost:8000/api`**

### 1. `GET /health` — Health Check

```bash
curl http://localhost:8000/health
```

```json
{"status": "ok"}
```

### 2. `GET /api/stats` — Database Statistics

```bash
curl http://localhost:8000/api/stats
```

```json
{
  "total_products": 4206,
  "total_categories": 172,
  "total_brands": 82,
  "last_sync": "2026-07-09T14:52:25.720298"
}
```

### 3. `GET /api/categories` — Full Category Hierarchy

```bash
curl http://localhost:8000/api/categories
```

```json
[
  {
    "id": 22,
    "parent_id": null,
    "name": "Audio components",
    "slug": "audio-components",
    "children": [
      {
        "id": 23,
        "parent_id": 22,
        "name": "Woofers",
        "slug": "woofers",
        "children": [
          {"id": 24, "parent_id": 23, "name": "Full-range woofer", "slug": "full-range-woofer", "children": []},
          {"id": 25, "parent_id": 23, "name": "Subwoofer", "slug": "subwoofer", "children": []}
        ]
      }
    ]
  }
]
```

### 4. `GET /api/brands` — All Brands with Product Counts

```bash
curl http://localhost:8000/api/brands
```

```json
[
  {"name": "Accuton",    "product_count": 9},
  {"name": "Dayton Audio","product_count": 969},
  {"name": "HiVi",       "product_count": 44},
  ...
]
```

### 5. `GET /api/products?page=1&limit=100` — Paginated Product List

**Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `page` | int | 1 | Page number |
| `limit` | int | 100 | Items per page (max 500) |
| `brand` | string | — | Filter by brand name |
| `category` | string | — | Filter by category slug |
| `search` | string | — | Search in title/SKU/brand |
| `sort_by` | string | `updated_at` | `sku`, `title`, `price`, `brand`, `stock` |
| `sort_order` | string | `desc` | `asc` or `desc` |

**Examples:**

```bash
# All products, first page
curl "http://localhost:8000/api/products?limit=5"

# Filter by brand, sorted by price ascending
curl "http://localhost:8000/api/products?brand=Dayton%20Audio&sort_by=price&sort_order=asc"

# Search for "speaker" in title/SKU/brand
curl "http://localhost:8000/api/products?search=speaker&limit=10"

# Filter by category, sorted by stock descending
curl "http://localhost:8000/api/products?category=woofers&sort_by=stock&sort_order=desc"
```

**Response:**

```json
{
  "total": 4206,
  "page": 1,
  "limit": 2,
  "products": [
    {
      "id": 913,
      "sku": "18W/8531G00",
      "title": "Revelator 18W/8531G00 7\" Woofer",
      "regular_price": 214.95,
      "stock_status": "in_stock",
      "brand": "Scan-Speak",
      "updated_at": "2026-07-09T14:46:46"
    },
    {
      "id": 296,
      "sku": "15W/8530K00",
      "title": "Revelator 15W/8530K00 5.5'' Midwoofer",
      "regular_price": 199.95,
      "stock_status": "in_stock",
      "brand": "Scan-Speak",
      "updated_at": "2026-07-09T14:46:46"
    }
  ]
}
```

### 6. `GET /api/product/{id}` — Full Product Detail (Most Important)

```bash
curl http://localhost:8000/api/product/1
```

```json
{
  "id": 1,
  "sku": "PT6816-8",
  "title": "PT6816-8 Planar Tweeter",
  "regular_price": 69.95,
  "short_description": "Capable of delivering wide bandwidth with low distortion...",
  "long_description": "The GRS PT6816-8 is a planar magnetic tweeter designed for high-end audio applications...",
  "stock": 50,
  "stock_status": "in_stock",
  "brand": "GRS",
  "ean": null,
  "currency": "EUR",
  "url": "https://www.soundimports.eu/en/pt6816-8.html",
  "categories": ["audio-components/tweeters/planar-tweeter"],
  "images": [
    {"id": 1, "src": "https://www.soundimports.eu/media/image/pt6816-8.jpg", "sort_order": 0, "is_cover": true},
    {"id": 2, "src": "https://www.soundimports.eu/media/image/pt6816-8-1.jpg", "sort_order": 1, "is_cover": false}
  ],
  "attributes": [
    {"name": "Impedance", "value": "8 Ohm"},
    {"name": "Power Handling", "value": "50 W"},
    {"name": "Frequency Response", "value": "500 Hz - 30 kHz"}
  ],
  "updated_at": "2026-07-09T14:46:46"
}
```

### 7. `GET /api/product/sku/{sku}` — Product Detail by SKU

```bash
curl http://localhost:8000/api/product/sku/PT6816-8
```

Returns the same format as `/api/product/{id}`.

### 8. `GET /api/products/changed?since=YYYY-MM-DD` — Changed Product IDs

```bash
curl "http://localhost:8000/api/products/changed?since=2026-07-01"
```

```json
{
  "since": "2026-07-01",
  "total": 4206,
  "product_ids": [913, 296, 306, 96, 977, 54, ...]
}
```

---

## Testing the API

### Option 1: Swagger UI (Browser — Easiest)

Open **http://localhost:8000/docs** in any browser. Every endpoint has:
- Interactive "Try it out" button
- Parameter documentation
- Schema examples
- Response codes

### Option 2: PowerShell (Windows)

```powershell
# Health check
Invoke-RestMethod "http://localhost:8000/health"

# Stats
Invoke-RestMethod "http://localhost:8000/api/stats" | ConvertTo-Json

# Categories
Invoke-RestMethod "http://localhost:8000/api/categories" | ConvertTo-Json -Depth 5

# Brands
Invoke-RestMethod "http://localhost:8000/api/brands" | ConvertTo-Json

# Products — filtered, sorted, paginated
$r = Invoke-RestMethod "http://localhost:8000/api/products?brand=HiVi&sort_by=price&sort_order=asc&limit=5"
$r.products | Format-Table id, sku, regular_price, title -AutoSize

# Full product detail
Invoke-RestMethod "http://localhost:8000/api/product/1" | ConvertTo-Json -Depth 10

# Product by SKU
Invoke-RestMethod "http://localhost:8000/api/product/sku/PT6816-8" | ConvertTo-Json -Depth 5

# Changed products since date
Invoke-RestMethod "http://localhost:8000/api/products/changed?since=2026-01-01" | ConvertTo-Json
```

### Option 3: curl (All Platforms)

```bash
curl -s http://localhost:8000/api/stats | python -m json.tool
curl -s "http://localhost:8000/api/products?limit=3" | python -m json.tool
curl -s http://localhost:8000/api/product/1 | python -m json.tool
curl -s "http://localhost:8000/api/products/changed?since=2026-01-01" | python -m json.tool
```

### Option 4: Pytest (Automated Test Suite)

```bash
# Run all 21 tests
pytest tests/ -v

# With coverage
pytest tests/ --cov=app --cov=scraper
```

---

## Downloading Data (JSON Export)

### Method 1: Via API

```powershell
# All 4,206 products to a single JSON file
Invoke-RestMethod "http://localhost:8000/api/products?limit=5000" -OutFile products.json

# Filtered: Dayton Audio products sorted by price
Invoke-RestMethod "http://localhost:8000/api/products?brand=Dayton%20Audio&sort_by=price&sort_order=asc&limit=500" -OutFile dayton_audio.json

# Full product detail for a specific product
Invoke-RestMethod "http://localhost:8000/api/product/1" | ConvertTo-Json -Depth 10 > product_1.json

# Categories
Invoke-RestMethod "http://localhost:8000/api/categories" -OutFile categories.json

# Brands
Invoke-RestMethod "http://localhost:8000/api/brands" -OutFile brands.json
```

### Method 2: Via CLI Export

```bash
# Export all products with clean API field names
docker compose run --rm scraper export -o products.json

# Filtered, sorted
docker compose run --rm scraper export -o dayton.json --brand "Dayton Audio" --sort-by price --sort-order asc

# From Docker container to host
docker compose cp scraper:/app/products.json .
```

### Method 3: Auto-Exported Files (Generated After Each Scrape)

After the scraper completes, these files are created in the `export/` directory:

```
export/
├── categories.json          # All 172 categories in hierarchy
├── brands.json              # All 82 brands with product counts
└── products/
    ├── HI-VI-OS-10.json     # One file per product (named by SKU)
    ├── PT6816-8.json
    ├── DAYTON-AUDIO-8.json
    └── ... (~4,200 files)
```

These files use the **same clean field names** as the API, so they're ready for import into any system.

---

## WordPress Plugin Integration

The API is designed to be consumed by a WordPress plugin. Below is a complete plugin that syncs products from the SoundImports Scraper API into WooCommerce.

### Complete WordPress Plugin Code

Create a file `wp-content/plugins/soundimports-sync/soundimports-sync.php`:

```php
<?php
/**
 * Plugin Name: SoundImports Sync
 * Description: Syncs products from SoundImports Scraper API into WooCommerce.
 * Version: 1.0.0
 * Author: Your Name
 * Requires: WooCommerce 6.0+
 */

// Prevent direct access
defined('ABSPATH') or die('No direct access');

// Configuration
define('SI_API_URL', 'http://YOUR_SERVER_IP:8000/api'); // ← Change this to your server
define('SI_SYNC_LIMIT', 100); // Products per sync batch

// ============================================================
// 1. HELPER FUNCTIONS — Call the SoundImports API
// ============================================================

/**
 * Make a GET request to the SoundImports API.
 */
function si_api_get($endpoint) {
    $response = wp_remote_get(SI_API_URL . '/' . ltrim($endpoint, '/'), [
        'timeout' => 30,
        'headers' => ['Accept' => 'application/json'],
    ]);
    if (is_wp_error($response) || wp_remote_retrieve_response_code($response) !== 200) {
        return null;
    }
    return json_decode(wp_remote_retrieve_body($response), true);
}

/**
 * Get paginated products from the API.
 */
function si_get_products($page = 1, $limit = 100, $brand = '') {
    $url = "/products?page={$page}&limit={$limit}";
    if ($brand) $url .= '&brand=' . urlencode($brand);
    return si_api_get($url);
}

/**
 * Get a single product by SKU.
 */
function si_get_product($sku) {
    return si_api_get('/product/sku/' . urlencode($sku));
}

/**
 * Get all categories.
 */
function si_get_categories() {
    return si_api_get('/categories');
}

/**
 * Get all brands.
 */
function si_get_brands() {
    return si_api_get('/brands');
}

/**
 * Get database statistics.
 */
function si_get_stats() {
    return si_api_get('/stats');
}

/**
 * Get changed product IDs since a date.
 */
function si_get_changed($since_date) {
    return si_api_get('/products/changed?since=' . $since_date);
}

/**
 * Trigger a scrape on the server.
 */
function si_trigger_sync() {
    $response = wp_remote_post(SI_API_URL . '/sync', [
        'method' => 'POST',
        'timeout' => 5,
    ]);
    if (is_wp_error($response)) return null;
    return json_decode(wp_remote_retrieve_body($response), true);
}

// ============================================================
// 2. SYNC FUNCTIONS — Import products into WooCommerce
// ============================================================

/**
 * Import a single product from the API into WooCommerce.
 */
function si_import_product($api_product) {
    // Check if product already exists by SKU
    $existing_id = wc_get_product_id_by_sku($api_product['sku']);
    
    if ($existing_id) {
        $product = wc_get_product($existing_id);
    } else {
        $product = new WC_Product_Simple();
    }

    // Basic fields
    $product->set_name($api_product['title']);
    $product->set_sku($api_product['sku']);
    $product->set_regular_price($api_product['regular_price']);
    $product->set_stock_quantity($api_product['stock']);
    $product->set_stock_status($api_product['stock_status']);
    $product->set_short_description($api_product['short_description'] ?? '');
    $product->set_description($api_product['long_description'] ?? '');
    
    // Save the product
    $product_id = $product->save();

    // Import categories (create terms if needed)
    if (!empty($api_product['categories'])) {
        $term_ids = [];
        foreach ($api_product['categories'] as $category_slug) {
            $term = term_exists($category_slug, 'product_cat');
            if (!$term) {
                $name = ucwords(str_replace('-', ' ', basename($category_slug)));
                $term = wp_insert_term($name, 'product_cat', ['slug' => $category_slug]);
            }
            if (!is_wp_error($term)) {
                $term_ids[] = is_array($term) ? $term['term_id'] : $term;
            }
        }
        wp_set_object_terms($product_id, $term_ids, 'product_cat');
    }

    // Import images
    if (!empty($api_product['images'])) {
        $image_ids = [];
        foreach ($api_product['images'] as $img) {
            $image_id = si_upload_image($img['src'], $product_id);
            if ($image_id) {
                $image_ids[] = $image_id;
            }
        }
        if (!empty($image_ids)) {
            // Set first image as featured
            set_post_thumbnail($product_id, $image_ids[0]);
            // Set remaining as gallery
            if (count($image_ids) > 1) {
                update_post_meta($product_id, '_product_image_gallery', implode(',', array_slice($image_ids, 1)));
            }
        }
    }

    // Import attributes
    if (!empty($api_product['attributes'])) {
        $product_attrs = [];
        foreach ($api_product['attributes'] as $attr) {
            $product_attrs[$attr['name']] = [
                'name'         => $attr['name'],
                'value'        => $attr['value'],
                'position'     => 0,
                'is_visible'   => 1,
                'is_variation' => 0,
                'is_taxonomy'  => 0,
            ];
        }
        update_post_meta($product_id, '_product_attributes', $product_attrs);
    }

    return $product_id;
}

/**
 * Upload an image from URL to WordPress media library.
 */
function si_upload_image($image_url, $parent_id = 0) {
    // Check if image already exists
    $attachment_id = attachment_url_to_postid($image_url);
    if ($attachment_id) return $attachment_id;

    require_once ABSPATH . 'wp-admin/includes/media.php';
    require_once ABSPATH . 'wp-admin/includes/file.php';
    require_once ABSPATH . 'wp-admin/includes/image.php';

    $tmp = download_url($image_url);
    if (is_wp_error($tmp)) return false;

    $file_array = [
        'name'     => basename($image_url),
        'tmp_name' => $tmp,
    ];

    $attachment_id = media_handle_sideload($file_array, $parent_id);
    if (is_wp_error($attachment_id)) {
        @unlink($tmp);
        return false;
    }
    return $attachment_id;
}

// ============================================================
// 3. ADMIN PAGE — Manual sync trigger
// ============================================================

add_action('admin_menu', function () {
    add_management_page(
        'SoundImports Sync',
        'SoundImports Sync',
        'manage_options',
        'soundimports-sync',
        'si_admin_page'
    );
});

function si_admin_page() {
    $stats = si_get_stats();
    ?>
    <div class="wrap">
        <h1>SoundImports Sync</h1>
        
        <h2>Database Statistics</h2>
        <table class="widefat striped">
            <tr><td>Products</td><td><?php echo esc_html($stats['total_products'] ?? 'N/A'); ?></td></tr>
            <tr><td>Categories</td><td><?php echo esc_html($stats['total_categories'] ?? 'N/A'); ?></td></tr>
            <tr><td>Brands</td><td><?php echo esc_html($stats['total_brands'] ?? 'N/A'); ?></td></tr>
            <tr><td>Last Sync</td><td><?php echo esc_html($stats['last_sync'] ?? 'Never'); ?></td></tr>
        </table>

        <h2>Actions</h2>
        <form method="post">
            <button type="submit" name="si_sync_all" class="button button-primary">
                Sync All Products to WooCommerce
            </button>
            <button type="submit" name="si_trigger_scrape" class="button">
                Trigger Scrape on Server
            </button>
        </form>
        <?php
        if (isset($_POST['si_sync_all'])) {
            si_run_sync();
        }
        if (isset($_POST['si_trigger_scrape'])) {
            $result = si_trigger_sync();
            echo '<p>Scrape triggered: ' . esc_html($result['message'] ?? 'Done') . '</p>';
        }
        ?>
    </div>
    <?php
}

/**
 * Run a full sync of all products.
 */
function si_run_sync() {
    echo '<div class="notice notice-info"><p>Starting sync...</p></div>';
    $page = 1;
    $total = 0;
    do {
        $data = si_get_products($page, SI_SYNC_LIMIT);
        if (!$data || empty($data['products'])) break;
        
        foreach ($data['products'] as $api_product) {
            // Get full detail for each product
            $detail = si_get_product($api_product['sku']);
            if ($detail) {
                si_import_product($detail);
                $total++;
            }
        }
        $page++;
        echo '<p>Page ' . ($page - 1) . ' done (' . count($data['products']) . ' products)...</p>';
    } while (count($data['products']) === SI_SYNC_LIMIT);
    
    echo '<div class="notice notice-success"><p>Sync complete! ' . $total . ' products imported.</p></div>';
}
```

### Field Mapping

| API Field | WooCommerce Field | Notes |
|-----------|------------------|-------|
| `sku` | `_sku` | Used for deduplication |
| `regular_price` | `_regular_price` | Direct mapping |
| `short_description` | `post_excerpt` | Shown in product listings |
| `long_description` | `post_content` | Full product description |
| `stock` | `_stock` | Stock quantity |
| `stock_status` | `_stock_status` | `in_stock` / `out_of_stock` |
| `images[].src` | Featured image + gallery | First image = featured |
| `categories` | `product_cat` taxonomy | Auto-creates terms |
| `attributes` | Product attributes | Stored as custom attributes |

### Installation

1. Create the folder `wp-content/plugins/soundimports-sync/`
2. Save the plugin code as `soundimports-sync.php`
3. In WordPress admin: **Plugins → Installed Plugins → Activate "SoundImports Sync"**
4. Go to **Tools → SoundImports Sync** to view stats and run sync

---

## Project Structure

```
soundimports-scraper/
│
├── app/                           # FastAPI REST API
│   ├── main.py                    # App entry point, lifespan, router prefix
│   ├── config.py                  # Pydantic settings (reads .env)
│   ├── database.py                # SQLAlchemy async engine (PostgreSQL + SQLite)
│   ├── models.py                  # ORM models: Category, Product, Image, Attribute...
│   ├── schemas.py                 # Pydantic v2 schemas (the API contract)
│   ├── crud.py                    # Database operations (upsert, query, pagination)
│   ├── router.py                  # 8 API route definitions
│   └── dependencies.py            # FastAPI dependency injection (get_db)
│
├── scraper/                       # Scraping engine
│   ├── base.py                    # Abstract BaseSupplierScraper (extensible)
│   ├── client.py                  # aiohttp wrapper with retry + semaphore
│   ├── sitemap.py                 # HTML sitemap parser (BeautifulSoup)
│   ├── category.py                # Category product-list fetcher
│   ├── product.py                 # Product detail fetcher + JSON normalizer
│   ├── pipeline.py                # Orchestrator + JSON file export
│   ├── soundimports.py            # Concrete SoundImports implementation
│   └── cli.py                     # Click CLI (scrape, export, serve, etc.)
│
├── tests/                         # 21 pytest tests
│   ├── conftest.py                # Fixtures (sample JSON, HTML)
│   ├── test_sitemap.py            # Sitemap parser tests
│   ├── test_category.py           # Category scraper tests
│   ├── test_product.py            # Product detail parser tests
│   └── test_crud.py               # Database CRUD tests
│
├── alembic/                       # Database migrations
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       ├── 001_initial.py         # Initial schema
│       └── 002_add_new_fields.py  # Added regular_price, short_description
│
├── export/                        # Auto-generated JSON files after scrape
│   ├── categories.json            # All 172 categories
│   ├── brands.json                # All 82 brands
│   └── products/{sku}.json        # One file per product
│
├── docker-compose.yml             # PostgreSQL + API + Scraper
├── Dockerfile                     # Python 3.12 slim image
├── requirements.txt               # Python dependencies
├── .env.example                   # Configuration template
└── README.md                      # This file
```

---

## Configuration

All settings via environment variables or `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://scraper:scraper_pass@localhost:5432/soundimports` | PostgreSQL (production) |
| `DATABASE_URL` | `sqlite+aiosqlite:///./soundimports.db` | SQLite (development — no Docker needed) |
| `BASE_URL` | `https://www.soundimports.eu` | SoundImports base URL |
| `SITEMAP_URL` | `https://www.soundimports.eu/en/sitemap/` | Sitemap for category discovery |
| `CONCURRENCY` | `50` | Max concurrent HTTP requests |
| `MAX_RETRIES` | `5` | Retry attempts per failed request |
| `API_HOST` | `0.0.0.0` | FastAPI bind address |
| `API_PORT` | `8000` | FastAPI port |
| `JSON_EXPORT_DIR` | `export` | Directory for auto-generated JSON files |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

---

## Database Schema

### `products` table (key columns)

| Column | Type | API Field | Description |
|--------|------|-----------|-------------|
| `id` | int (PK) | `id` | Auto-increment |
| `sku` | varchar(200) | `sku` | **Unique** — upsert key |
| `ean` | varchar(50) | `ean` | EAN/UPC barcode |
| `title` | varchar(1000) | `title` | Product name |
| `regular_price` | float | `regular_price` | Current price |
| `short_description` | text | `short_description` | Short description |
| `long_description` | text | `long_description` | Full description |
| `stock` | int | `stock` | Stock quantity |
| `stock_status` | varchar(50) | `stock_status` | `in_stock`, `out_of_stock` |
| `brand` | varchar(500) | `brand` | Brand name |
| `raw_json` | text | *(never exposed)* | Full supplier JSON (future-proof) |
| `category_ids` | text | `categories` | Comma-separated category slugs |

### Related tables

- **`categories`** — Self-referencing hierarchy (parent_id → id)
- **`images`** — Product images with sort_order and is_cover flag
- **`attributes`** — Key-value technical specs (e.g., Impedance → "8 Ohm")
- **`scrape_jobs`** — Scrape run history with status, counts, timing
- **`scrape_progress`** — Per-category progress tracking for resume support

---

## License

MIT
