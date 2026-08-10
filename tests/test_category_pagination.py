import pytest

from scraper.category import CategoryScraper


class RecordingClient:
    def __init__(self):
        self.calls = []

    async def fetch_json(self, url, params=None):
        self.calls.append((url, params))
        return {"collection": {"products": []}}


@pytest.mark.asyncio
async def test_category_page_number_is_encoded_in_path():
    client = RecordingClient()
    scraper = CategoryScraper(client)

    await scraper.fetch_page("https://www.soundimports.eu/en/coils/", page=1)
    await scraper.fetch_page("https://www.soundimports.eu/en/coils/", page=2)

    assert client.calls == [
        (
            "https://www.soundimports.eu/en/coils/",
            {"format": "json", "limit": 100},
        ),
        (
            "https://www.soundimports.eu/en/coils/page2.html",
            {"format": "json", "limit": 100},
        ),
    ]
