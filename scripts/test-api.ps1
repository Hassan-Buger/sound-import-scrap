param([string]$BaseUrl = "http://localhost:8001")

$ErrorActionPreference = "Stop"
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12

function Test-Endpoint {
    param($Name, $Method = "GET", $Uri, $Body = $null)
    try {
        if ($Method -eq "GET") {
            $resp = Invoke-WebRequest -Uri "$BaseUrl$Uri" -UseBasicParsing
        } else {
            $resp = Invoke-WebRequest -Uri "$BaseUrl$Uri" -Method $Method -Body $Body -ContentType "application/json" -UseBasicParsing
        }
        $raw = $resp.Content
        $len = $raw.Length
        if ($len -gt 500) { $preview = $raw.Substring(0, 200) + "...`n...`n" + $raw.Substring($raw.Length - 200) }
        else { $preview = $raw }
        Write-Host "`n=== $Name ===" -ForegroundColor Green
        Write-Host "  $Method $Uri -> $($resp.StatusCode) ($len bytes)" -ForegroundColor Cyan
        Write-Host $preview -ForegroundColor Gray
    } catch {
        Write-Host "`n=== $Name ===" -ForegroundColor Red
        Write-Host "  $Method $Uri -> ERROR: $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host "========================================" -ForegroundColor Yellow
Write-Host "  SoundImports API Test - $BaseUrl" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow

Test-Endpoint "Health" -Uri "/health"
Test-Endpoint "Stats" -Uri "/api/stats"
Test-Endpoint "Categories" -Uri "/api/categories"
Test-Endpoint "Brands" -Uri "/api/brands"
Test-Endpoint "Products (page 1, 2 items)" -Uri "/api/products?limit=2"
Test-Endpoint "Product Detail (ID 1)" -Uri "/api/product/1"
Test-Endpoint "Product Description (ID 1)" -Uri "/api/product/1/description"
Test-Endpoint "Product by SKU" -Uri "/api/product/sku/RS180P-8"
Test-Endpoint "Product by SKU with slash" -Uri "/api/product/sku/FL4RCN%2FF"
Test-Endpoint "Changed Products" -Uri "/api/products/changed?since=2026-07-26"
Test-Endpoint "Filter by brand" -Uri "/api/products?brand=Dayton%20Audio&limit=2"
Test-Endpoint "Filter by category" -Uri "/api/products?category=tweeters&limit=2"
Test-Endpoint "Product not found (expect 404)" -Uri "/api/product/99999"
Test-Endpoint "Empty filter (expect 0 results)" -Uri "/api/products?brand=Nonexistent"
