import pytest
from scraper.sitemap import SitemapParser


def test_parse_html(sample_sitemap_html):
    """Test that sitemap HTML is correctly parsed into categories."""
    parser = SitemapParser.__new__(SitemapParser)
    result = parser._parse_html(sample_sitemap_html)

    assert len(result) >= 6

    home_audio = [c for c in result if c["slug"] == "home-audio"]
    assert len(home_audio) > 0
    assert home_audio[0]["level"] == 1
    assert home_audio[0]["parent_path"] is None

    speakers = [c for c in result if c["slug"] == "speakers"]
    assert len(speakers) > 0
    assert speakers[0]["level"] == 2
    assert speakers[0]["parent_path"] == "/en/home-audio/"

    bookshelf = [c for c in result if c["slug"] == "bookshelf-speakers"]
    assert len(bookshelf) > 0
    assert bookshelf[0]["level"] == 3
    assert bookshelf[0]["parent_path"] == "/en/home-audio/speakers/"

    amplifiers = [c for c in result if c["slug"] == "amplifiers"]
    assert len(amplifiers) > 0
    assert amplifiers[0]["level"] == 2
    assert amplifiers[0]["parent_path"] == "/en/home-audio/"


def test_brands_not_in_categories(sample_sitemap_html):
    """Test that brand links are not included in category results."""
    parser = SitemapParser.__new__(SitemapParser)
    result = parser._parse_html(sample_sitemap_html)

    brand_slugs = [c["slug"] for c in result if "brand" in c["slug"]]
    assert len(brand_slugs) == 0


def test_name_cleaning():
    """Test that product counts are stripped from names."""
    parser = SitemapParser.__new__(SitemapParser)
    assert parser._clean_name("Home audio (510)") == "Home audio"
    assert parser._clean_name("Speakers (114)") == "Speakers"
    assert parser._clean_name("No Number") == "No Number"
    assert parser._clean_name("") == ""


def test_extract_slug():
    """Test URL slug extraction."""
    parser = SitemapParser.__new__(SitemapParser)

    assert (
        parser._extract_slug("https://www.soundimports.eu/en/home-audio/speakers/")
        == "speakers"
    )
    assert (
        parser._extract_slug("https://www.soundimports.eu/en/home-audio/")
        == "home-audio"
    )
    assert parser._extract_slug("/en/brands/yamaha/") == "yamaha"


def test_parse_empty_html():
    """Test that empty HTML returns no categories."""
    parser = SitemapParser.__new__(SitemapParser)
    result = parser._parse_html("<html><body></body></html>")
    assert result == []


def test_parse_no_categories_section():
    """Test that HTML without categories section returns empty."""
    parser = SitemapParser.__new__(SitemapParser)
    html = "<html><body><div>No categories here</div></body></html>"
    result = parser._parse_html(html)
    assert result == []
