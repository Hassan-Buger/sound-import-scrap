import pytest
from typing import AsyncGenerator, Dict, Any

from app.config import settings


@pytest.fixture
def sample_product_detail_json() -> Dict[str, Any]:
    """Sample product detail JSON as returned by SoundImports."""
    return {
        "id": 12345,
        "sku": "HI-VI-OS-10",
        "ean": "6923527272722",
        "name": "HiVi OS-10",
        "title": "HiVi OS-10 2-Way Bookshelf Speaker",
        "description": "The HiVi OS-10 is a high-quality 2-way bookshelf speaker.",
        "longDescription": "Detailed description of the HiVi OS-10 speaker with technical specifications.",
        "price": {"amount": 299.00, "currency": "EUR"},
        "stock": {"quantity": 15, "status": "in_stock"},
        "brand": {"name": "HiVi", "title": "HiVi"},
        "url": "https://www.soundimports.eu/en/hivi-os-10.html",
        "cover": "https://www.soundimports.eu/media/image/hivi-os-10.jpg",
        "images": [
            {"url": "https://www.soundimports.eu/media/image/hivi-os-10-1.jpg"},
            {"url": "https://www.soundimports.eu/media/image/hivi-os-10-2.jpg"},
        ],
        "attributes": {
            "Impedance": "8 Ohm",
            "Sensitivity": "88 dB",
            "Frequency Response": "40 Hz - 20 kHz",
            "Power Handling": "100 W",
        },
        "categories": {
            "123": {"id": 123, "url": "home-audio/speakers/bookshelf-speakers", "title": "Bookshelf Speakers"}
        },
    }


@pytest.fixture
def sample_category_json() -> Dict[str, Any]:
    """Sample category JSON response with product list."""
    return {
        "total": 150,
        "limit": 100,
        "page": 1,
        "products": [
            {
                "id": 1001,
                "sku": "PROD-001",
                "name": "Product One",
                "price": 99.99,
                "url": "https://www.soundimports.eu/en/product-one.html",
            },
            {
                "id": 1002,
                "sku": "PROD-002",
                "name": "Product Two",
                "price": 149.99,
                "url": "https://www.soundimports.eu/en/product-two.html",
            },
        ],
    }


@pytest.fixture
def sample_sitemap_html() -> str:
    """Sample sitemap HTML matching the actual SoundImports structure."""
    return """
    <html><body>
    <div class="gui-list" role="group" aria-labelledby="gui-sitemap-group-categories-title">
      <strong role="heading" aria-level="2" id="gui-sitemap-group-categories-title">Categories:</strong>
      <ul>
        <li><a href="https://www.soundimports.eu/en/home-audio/" title="Home audio">Home audio <span>(510)</span></a>
          <ul>
            <li><a href="https://www.soundimports.eu/en/home-audio/speakers/" title="Speakers">Speakers <span>(114)</span></a>
              <ul>
                <li><a href="https://www.soundimports.eu/en/home-audio/speakers/bookshelf-speakers/" title="Bookshelf speakers">Bookshelf speakers <span>(52)</span></a></li>
                <li><a href="https://www.soundimports.eu/en/home-audio/speakers/tower-speakers/" title="Tower speakers">Tower speakers <span>(6)</span></a></li>
              </ul>
            </li>
            <li><a href="https://www.soundimports.eu/en/home-audio/amplifiers/" title="Amplifiers">Amplifiers <span>(214)</span></a></li>
          </ul>
        </li>
        <li><a href="https://www.soundimports.eu/en/audio-components/" title="Audio components">Audio components <span>(2604)</span></a>
          <ul>
            <li><a href="https://www.soundimports.eu/en/audio-components/woofers/" title="Woofers">Woofers <span>(1684)</span></a></li>
          </ul>
        </li>
        <li><a href="https://www.soundimports.eu/en/crossover-components/" title="Crossover components">Crossover components <span>(5029)</span></a></li>
      </ul>
    </div>
    <div class="gui-list" role="group" aria-labelledby="gui-sitemap-group-brands-title">
      <strong role="heading" aria-level="2" id="gui-sitemap-group-brands-title">All brands:</strong>
      <ul>
        <li><a href="https://www.soundimports.eu/en/brands/accuton/">Accuton <span>(50)</span></a></li>
        <li><a href="https://www.soundimports.eu/en/brands/dayton-audio/">Dayton Audio <span>(969)</span></a></li>
      </ul>
    </div>
    </body></html>
    """
