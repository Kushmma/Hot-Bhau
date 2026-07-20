"""
GBN Store Smartphone Scraper (Selenium version)
================================================
Scrapes ALL smartphone listings from:
    https://www.gbnstore.com/category/smartphone

The page only server-renders the first 20 products. The rest load
when the "View more products" button is clicked (client-side JS).
This script uses Selenium to click that button repeatedly until all
products are loaded, then parses the final HTML with BeautifulSoup.

Install dependencies:
    pip install selenium beautifulsoup4 pandas webdriver-manager

Run:
    python gbnstore_scraper_selenium.py
"""

import time
import csv
import re
from dataclasses import dataclass, asdict
from typing import List, Optional

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    ElementClickInterceptedException,
    StaleElementReferenceException,
)

try:
    from webdriver_manager.chrome import ChromeDriverManager
    HAVE_WDM = True
except ImportError:
    HAVE_WDM = False

URL = "https://www.gbnstore.com/category/smartphone"


@dataclass
class Product:
    name: str
    brand: Optional[str]
    price: Optional[str]
    original_price: Optional[str]
    discount: Optional[str]
    stock_status: Optional[str]
    emi_available: bool
    product_url: str


def build_driver(headless: bool = True) -> webdriver.Chrome:
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1400,2000")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

    if HAVE_WDM:
        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=options)
    # Falls back to chromedriver already on PATH
    return webdriver.Chrome(options=options)


def load_all_products(driver: webdriver.Chrome, max_clicks: int = 30, pause: float = 1.5) -> str:
    """Load the category page and keep clicking 'View more products'
    until every product is present (or the button disappears)."""
    driver.get(URL)

    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/product/']"))
    )

    clicks = 0
    while clicks < max_clicks:
        # Count products currently on the page
        cards_before = len(driver.find_elements(By.CSS_SELECTOR, "a[href*='/product/']"))

        # Try to find the "View more products" button by visible text
        load_more = None
        for el in driver.find_elements(By.XPATH, "//*[self::button or self::a or self::div]"):
            try:
                if el.text.strip().lower() == "view more products":
                    load_more = el
                    break
            except StaleElementReferenceException:
                continue

        if load_more is None:
            break  # No more button -> all products loaded

        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", load_more)
            time.sleep(0.3)
            load_more.click()
        except ElementClickInterceptedException:
            driver.execute_script("arguments[0].click();", load_more)

        clicks += 1
        time.sleep(pause)

        # Wait until more product cards appear, or give up after a timeout
        try:
            WebDriverWait(driver, 10).until(
                lambda d: len(d.find_elements(By.CSS_SELECTOR, "a[href*='/product/']")) > cards_before
            )
        except TimeoutException:
            break

    return driver.page_source


def parse_products(html: str) -> List[Product]:
    soup = BeautifulSoup(html, "html.parser")
    products = []
    seen_urls = set()

    # Each product card is anchored by a link to /product/<slug>
    # We locate the "View details" links, then walk up to the card container.
    detail_links = soup.select("a[href*='/product/']")

    for link in detail_links:
        href = link.get("href", "")
        if "/product/" not in href:
            continue
        product_url = href if href.startswith("http") else f"https://www.gbnstore.com{href}"
        if product_url in seen_urls:
            continue

        # Walk up to find the card wrapper that has price/name/stock info together
        card = link
        for _ in range(6):
            if card.parent is None:
                break
            card = card.parent
            text = card.get_text(" ", strip=True)
            if "Rs." in text and len(text) < 400:
                break

        card_text = card.get_text(" ", strip=True)

        # Name: prefer an element whose text doesn't start with a brand-duplicated pattern
        name_el = card.select_one("a[href*='/product/'] img")
        name = None
        if name_el and name_el.get("alt"):
            name = name_el["alt"].strip()
        if not name:
            # fall back to the first link text that looks like a product name
            for a in card.select("a[href*='/product/']"):
                t = a.get_text(strip=True)
                if t and "view details" not in t.lower():
                    name = t
                    break
        if not name:
            name = card_text[:80]

        # Prices: pattern like "Rs.53,999Rs.61,099" (current, then original)
        prices = re.findall(r"Rs\.[\d,]+", card_text)
        price = prices[0] if len(prices) >= 1 else None
        original_price = prices[1] if len(prices) >= 2 else None

        # Discount, e.g. "15% OFF"
        discount_match = re.search(r"(\d+%\s*OFF)", card_text, re.IGNORECASE)
        discount = discount_match.group(1) if discount_match else None

        stock_status = "In stock" if "In stock" in card_text else (
            "Out of stock" if "Out of stock" in card_text else None
        )
        emi_available = "EMI" in card_text

        # Brand: first word of the product name is usually the brand,
        # but some cards repeat "Brand Product Name" in a small tag before the link.
        brand = None
        brand_el = card.select_one("[class*='brand'], span")
        if brand_el:
            candidate = brand_el.get_text(strip=True)
            if candidate and len(candidate.split()) <= 2:
                brand = candidate

        products.append(
            Product(
                name=name,
                brand=brand,
                price=price,
                original_price=original_price,
                discount=discount,
                stock_status=stock_status,
                emi_available=emi_available,
                product_url=product_url,
            )
        )
        seen_urls.add(product_url)

    return products


def save_csv(products: List[Product], path: str = "gbnstore_smartphones.csv"):
    if not products:
        print("No products to save.")
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(products[0]).keys()))
        writer.writeheader()
        for p in products:
            writer.writerow(asdict(p))
    print(f"Saved {len(products)} products to {path}")


def main():
    driver = build_driver(headless=True)
    try:
        html = load_all_products(driver)
    finally:
        driver.quit()

    products = parse_products(html)
    print(f"Scraped {len(products)} products.")
    for p in products[:5]:
        print(p)

    save_csv(products)


if __name__ == "__main__":
    main()