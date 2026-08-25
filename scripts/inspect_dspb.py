import asyncio
import httpx
import json
import re

async def check():
    url = "https://www.soundimports.eu/en/dayton-audio-dspb-ec.html"
    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:
        r_json = await client.get(url, params={"format": "json"})
        data = r_json.json()
        prod = data.get("product", data)
        print("JSON stock:", prod.get("stock"))
        print("JSON inventory:", prod.get("inventory"))
        print("JSON availability:", prod.get("availability"))
        print("JSON stockStatus:", prod.get("stockStatus"))
        print("JSON availabilityText:", prod.get("availabilityText"))
        
        r_html = await client.get(url)
        html = r_html.text
        hurry = re.findall(r'<span[^>]*class="[^"]*hurry[^"]*"[^>]*>([^<]+)</span>', html)
        print("HTML hurry spans:", hurry)
        stock_matches = re.findall(r"(\d+)\s*\+?\s*in\s*stock", html, re.I)
        print("HTML in stock regex:", stock_matches)
        out_matches = re.findall(r"out\s*of\s*stock", html, re.I)
        print("HTML out of stock regex:", out_matches)

if __name__ == "__main__":
    asyncio.run(check())
