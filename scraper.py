"""
HatBhau (हट भाउ) — Enhanced Scraper Engine
==========================================
Enhanced to scrape ALL products from each source with real-time status tracking.
"""

import re
import sys
import time
import json
import requests
from datetime import datetime, timezone
from collections import deque
from urllib.parse import urljoin, urlparse, quote_plus
from concurrent.futures import ThreadPoolExecutor, as_completed

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

import database

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

# ── ROTATING HEADER POOL ─────────────────────────────────────────────────────
UA_POOL = [
    UA,
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/123.0.0.0 Safari/537.36"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0"),
]


def rotating_headers(extra=None):
    import random
    h = {
        "User-Agent": random.choice(UA_POOL),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    if extra:
        h.update(extra)
    return h


def fetch_with_retry(url, max_retries=3, timeout=20, **kwargs):
    """requests.get with exponential backoff + jitter."""
    import random
    kwargs.setdefault("headers", rotating_headers())
    kwargs.setdefault("timeout", timeout)
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            r = requests.get(url, **kwargs)
            if r.status_code >= 500:
                raise requests.HTTPError(f"{r.status_code} server error")
            return r
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
            last_exc = e
            if attempt == max_retries:
                break
            delay = (2 ** attempt) + random.uniform(0, 0.75)
            safe(f"[retry] {url} attempt {attempt + 1} failed ({e}); retrying in {delay:.1f}s")
            time.sleep(delay)
    raise last_exc


def safe(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(str(msg).encode("ascii", errors="replace").decode("ascii"))


def parse_price(text, min_p=100, max_p=1_000_000):
    """Parse a price out of arbitrary text."""
    if not text:
        return None
    text = str(text)
    m = re.search(r'(?:Rs\.?|NPR|INR|PKR|रू)\s*([\d,]+\.?\d*)', text, re.IGNORECASE)
    if m:
        try:
            v = float(m.group(1).replace(",", ""))
            if min_p <= v <= max_p:
                return v
        except ValueError:
            pass
    nums = re.findall(r'\b(\d{4,7})\b', text.replace(",", ""))
    for n in nums:
        v = float(n)
        if min_p <= v <= max_p:
            return v
    return None


def make_absolute(base_url, url):
    if not url:
        return ""
    if url.startswith("http"):
        return url
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return base_url.rstrip("/") + url
    return base_url.rstrip("/") + "/" + url


def clean_and_dedupe(raw_products, price_range):
    """Generic cleanup pass shared by every engine before persistence."""
    min_p, max_p = price_range
    cleaned, seen = [], set()
    JUNK = re.compile(
        r'\b(add to cart|quick view|sale|new arrival|in stock|out of stock|'
        r'sold out|buy now|shop now|view details|available|compare|wishlist|'
        r'free delivery|cod available|check price)\b', re.IGNORECASE)
    for item in raw_products:
        name = str(item.get("name", "")).strip()
        name = re.sub(r"[^\x20-\x7E\u0900-\u097F]", " ", name)
        name = JUNK.sub("", name)
        name = re.sub(r"\s+", " ", name).strip()
        if not name or len(name) < 4:
            continue
        try:
            price = float(item.get("price"))
        except (TypeError, ValueError):
            continue
        if not (min_p <= price <= max_p):
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        orig = item.get("original_price")
        try:
            orig = float(orig) if orig else None
        except (TypeError, ValueError):
            orig = None
        discount = round((1 - price / orig) * 100, 1) if orig and orig > price else None
        cleaned.append({
            "name": name[:250],
            "price": price,
            "original_price": orig,
            "discount_percent": discount,
            "url": (item.get("url") or "")[:600] or None,
            "image_url": (item.get("image_url") or "")[:600] or None,
            "category": item.get("category") or None,
            "rating": item.get("rating"),
            "reviews": item.get("reviews"),
            "availability": item.get("availability") or "In Stock",
        })
    return cleaned


def _get_driver():
    """Get a configured Selenium WebDriver instance."""
    opts = Options()
    if ENGINE_HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument(f"user-agent={UA}")
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=opts)
    except Exception as e:
        raise RuntimeError(
            "Selenium/Chrome could not start ("
            f"{type(e).__name__}: {e}). Most likely cause: Chrome/Chromium "
            "isn't installed on this machine, or ChromeDriverManager() "
            "couldn't reach the internet to download a matching driver. "
            "Run `google-chrome --version` (or `chromium --version`) to check, "
            "and confirm outbound network access to googlechromelabs.github.io."
        ) from e
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
    })
    return driver


def _human_scroll(driver, pause=0.25, max_steps=15):
    """Enhanced scrolling with better lazy-load detection."""
    last_height = 0
    for step in range(max_steps):
        prev = driver.execute_script("return document.body.scrollHeight")
        driver.execute_script("window.scrollBy(0, 900);")
        time.sleep(pause)
        new = driver.execute_script("return document.body.scrollHeight")
        pos = driver.execute_script("return window.pageYOffset + window.innerHeight")
        if new == prev and pos >= new - 100:
            break
        if new == last_height:
            break
        last_height = new
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.2)


def _click_load_more(driver, max_clicks=20):
    from selenium.webdriver.common.by import By
    selectors = [
        "button[data-action='load-more']", ".load-more-btn", "button.load-more",
        "[class*='load-more']", "[class*='loadMore']", "a.load-more",
        ".btn-load-more", "[class*='show-more']", "[class*='showMore']",
        ".pagination-next a", "[aria-label='Next']", ".next-page",
        "a.next", "button.next", ".page-next"
    ]
    clicks = 0
    for _ in range(max_clicks):
        clicked = False
        for sel in selectors:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, sel)
                if btn.is_displayed() and btn.is_enabled():
                    driver.execute_script("arguments[0].scrollIntoView(true);", btn)
                    time.sleep(0.2)
                    btn.click()
                    clicks += 1
                    time.sleep(1.2)
                    clicked = True
                    break
            except Exception:
                pass
        if not clicked:
            break
    return clicks


def _extract_by_selector_chain(soup_or_elem, selectors, get_attr=None):
    for sel in selectors:
        el = soup_or_elem.select_one(sel) if hasattr(soup_or_elem, 'select_one') else None
        if not el and hasattr(soup_or_elem, 'find_all'):
            el = soup_or_elem.find_all(sel)
            el = el[0] if el else None
        if el:
            if get_attr:
                val = el.get(get_attr)
                if val:
                    return val
            else:
                text = el.get_text(strip=True) if hasattr(el, 'get_text') else str(el).strip()
                if text:
                    return text
    return ""


# ── ENHANCED IMAGE EXTRACTION ──────────────────────────────────────────────

_LAZY_IMG_ATTRS = (
    "src", "data-src", "data-lazy-src", "data-original", "data-echo",
    "data-lazy", "data-image", "data-img", "data-srcset", "srcset",
    "data-zoom-image", "data-zoom", "data-large", "data-medium",
    "data-original-src", "data-src-large", "data-src-small",
    "data-image-url", "data-url", "data-full-url",
    "data-img-src", "data-org", "data-original-image",
    "data-main-image", "data-zoom-image", "data-big",
    "data-large-image", "data-high-res", "data-hd",
    "data-full", "data-original-url", "data-original-image-src",
    "data-src", "src", "data-ks-lazyload", "data-lazy",
    "data-original", "data-srcset",
    "data-zoom-src", "data-product-image-src",
)


def _pick_image_attr(img_tag):
    """Enhanced image attribute picker with better fallback handling."""
    if not img_tag:
        return None
        
    # Check if it's a picture element or has srcset
    if img_tag.name == "picture":
        sources = img_tag.find_all("source")
        for source in sources:
            srcset = source.get("srcset")
            if srcset:
                first_url = srcset.split(",")[0].strip().split(" ")[0]
                if first_url and not first_url.startswith("data:"):
                    return first_url
    
    # Try all lazy load attributes
    for attr in _LAZY_IMG_ATTRS:
        val = img_tag.get(attr)
        if not val:
            continue
        val = val.strip()
        
        if attr in ("srcset", "data-srcset"):
            urls = []
            for part in val.split(","):
                part = part.strip()
                if " " in part:
                    url, size = part.split(" ")
                    try:
                        if "x" in size:
                            multiplier = float(size.replace("x", ""))
                            urls.append((url, multiplier))
                        elif "w" in size:
                            width = int(size.replace("w", ""))
                            urls.append((url, width))
                    except:
                        urls.append((part.split(" ")[0], 0))
            if urls:
                urls.sort(key=lambda x: x[1], reverse=True)
                if urls[0][0] and not urls[0][0].startswith("data:"):
                    return urls[0][0]
        elif val.startswith("data:image"):
            continue
        elif val and not val.startswith("data:"):
            return val
            
    src = img_tag.get("src")
    if src and not src.startswith("data:"):
        return src
        
    for attr in ["content", "href", "srcset", "data-url", "data-image"]:
        val = img_tag.get(attr)
        if val and not val.startswith("data:") and ("http" in val or "/" in val):
            return val
            
    return None


def _extract_image(elem, selectors=None, base_url=""):
    """Enhanced image extraction with better fallback for all websites."""
    if selectors:
        for sel in selectors:
            try:
                if hasattr(elem, 'select_one'):
                    el = elem.select_one(sel)
                else:
                    el = elem.find(sel)
                if el:
                    if el.name == "picture":
                        img = el.find("img")
                        if img:
                            img_val = _pick_image_attr(img)
                            if img_val:
                                return make_absolute(base_url, img_val) if base_url else img_val
                        source = el.find("source")
                        if source:
                            srcset = source.get("srcset")
                            if srcset:
                                first_url = srcset.split(",")[0].strip().split(" ")[0]
                                if first_url and not first_url.startswith("data:"):
                                    return make_absolute(base_url, first_url) if base_url else first_url
                    else:
                        img_val = _pick_image_attr(el)
                        if img_val:
                            return make_absolute(base_url, img_val) if base_url else img_val
            except Exception:
                continue
    
    try:
        if hasattr(elem, 'find_all'):
            img_tags = elem.find_all("img")
        else:
            img_tags = elem.select("img")
            
        for img in img_tags:
            src = _pick_image_attr(img)
            if src:
                width = img.get("width")
                height = img.get("height")
                if width and height:
                    try:
                        if int(width) < 10 or int(height) < 10:
                            continue
                    except:
                        pass
                return make_absolute(base_url, src) if base_url else src
    except Exception:
        pass
    
    try:
        if hasattr(elem, 'parent'):
            parent = elem.parent
            if parent:
                img = parent.find("img")
                if img:
                    src = _pick_image_attr(img)
                    if src:
                        return make_absolute(base_url, src) if base_url else src
    except Exception:
        pass
    
    try:
        if hasattr(elem, 'find'):
            meta = elem.find("meta", property="og:image")
            if meta and meta.get("content"):
                return make_absolute(base_url, meta["content"]) if base_url else meta["content"]
    except Exception:
        pass
    
    return None


def _extract_daraz_image(elem, base_url=""):
    """Specialized image extraction for Daraz."""
    try:
        img = elem.find("img")
        if img:
            for attr in ["data-src", "src", "data-ks-lazyload", "data-lazy", "data-original"]:
                src = img.get(attr)
                if src and not src.startswith("data:") and "pixel" not in src.lower():
                    if src.startswith("//"):
                        return "https:" + src
                    return make_absolute(base_url, src) if base_url else src
                    
            srcset = img.get("srcset")
            if srcset:
                first_url = srcset.split(",")[0].strip().split(" ")[0]
                if first_url and not first_url.startswith("data:"):
                    return make_absolute(base_url, first_url) if base_url else first_url
                    
        style = elem.get("style", "")
        if "background-image" in style:
            match = re.search(r'url\(["\']?([^"\'\)]+)["\']?\)', style)
            if match:
                url = match.group(1)
                if url and not url.startswith("data:"):
                    return make_absolute(base_url, url) if base_url else url
    except Exception:
        pass
    return None


# ── DIAGNOSTIC ISSUE TRACKING ────────────────────────────────────────────────

_ENGINE_ISSUES: dict = {}


def note_issue(key, msg):
    """Record a diagnosable failure reason for source `key` and still print it."""
    _ENGINE_ISSUES.setdefault(key, []).append(str(msg)[:200])
    safe(f"[{key}] ISSUE: {msg}")


def _pop_issues(key):
    return _ENGINE_ISSUES.pop(key, [])


# ── SCRAPE STATUS TRACKING ──────────────────────────────────────────────────

_current_scrape_status = {
    "running": False,
    "current_store": None,
    "total_stores": 0,
    "completed_stores": 0,
    "store_status": {},
    "results": {},
    "started_at": None,
    "progress": 0,
}


def update_scrape_status(source_key=None, status=None, products=0, error=None):
    """Update the global scrape status for real-time UI updates."""
    global _current_scrape_status
    if source_key:
        if source_key not in _current_scrape_status["store_status"]:
            _current_scrape_status["store_status"][source_key] = {
                "status": "pending",
                "products": 0,
                "started_at": None,
                "finished_at": None,
                "error": None
            }
        
        if status:
            _current_scrape_status["store_status"][source_key]["status"] = status
            if status == "scraping":
                _current_scrape_status["store_status"][source_key]["started_at"] = datetime.now(timezone.utc).isoformat()
                _current_scrape_status["current_store"] = source_key
            elif status in ["done", "failed"]:
                _current_scrape_status["store_status"][source_key]["finished_at"] = datetime.now(timezone.utc).isoformat()
                if products:
                    _current_scrape_status["store_status"][source_key]["products"] = products
                if error:
                    _current_scrape_status["store_status"][source_key]["error"] = error
                _current_scrape_status["completed_stores"] = sum(
                    1 for s in _current_scrape_status["store_status"].values() 
                    if s["status"] in ["done", "failed"]
                )


def get_scrape_status():
    """Get the current scrape status for API responses."""
    global _current_scrape_status
    total = len(_current_scrape_status["store_status"])
    done = _current_scrape_status["completed_stores"]
    progress = int((done / total * 100)) if total > 0 else 0
    
    results = {}
    for key, info in _current_scrape_status["store_status"].items():
        if info["status"] in ["done", "failed"]:
            results[key] = {
                "status": info["status"],
                "count": info["products"],
                "finished_at": info["finished_at"],
                "error": info.get("error")
            }
    
    return {
        "running": _current_scrape_status["running"],
        "current_store": _current_scrape_status["current_store"],
        "total_stores": total,
        "completed_stores": done,
        "progress": progress,
        "store_status": _current_scrape_status["store_status"],
        "results": results,
        "message": _get_status_message(),
        "started_at": _current_scrape_status["started_at"]
    }


def _get_status_message():
    """Generate a human-readable status message."""
    global _current_scrape_status
    if not _current_scrape_status["running"]:
        if _current_scrape_status["completed_stores"] == len(_current_scrape_status["store_status"]) and len(_current_scrape_status["store_status"]) > 0:
            return "✅ All stores scraped successfully!"
        return "Scrape completed with some issues"
    
    current = _current_scrape_status["current_store"]
    done = _current_scrape_status["completed_stores"]
    total = len(_current_scrape_status["store_status"])
    
    if current:
        label = SITES.get(current, {}).get("label", current)
        return f"🔄 Scraping {label}... ({done}/{total} stores complete)"
    return f"🔄 Scraping in progress... ({done}/{total} stores complete)"


def reset_scrape_status():
    """Reset the scrape status for a new job."""
    global _current_scrape_status
    _current_scrape_status = {
        "running": False,
        "current_store": None,
        "total_stores": 0,
        "completed_stores": 0,
        "store_status": {},
        "results": {},
        "started_at": None,
        "progress": 0,
    }


# ── JS extractor used for PriceOye ─────────────────────────────────────────

PRICEOYE_EXTRACT_JS = r"""
const results = [];
const seen    = new Set();
const MIN     = 5000, MAX = 300000;
const CARD_SELS = [
    '.productCard', '.product-item', '.item-card',
    '[class*="ProductCard"]', '[class*="product-card"]',
    '[class*="listing"]', 'li[class*="item"]',
    '.pListingsSection li', '.categoryListing li',
    '.search-results li', '[data-product]',
];
const NAME_SELS = [
    'h3', 'h2', '.productTitle', '[class*="title"]',
    '[class*="name"]', '[class*="Title"]', 'a[title]',
];
const PRICE_SELS = [
    '.price', '[class*="price"]', '[class*="Price"]',
    '[class*="amount"]', '[class*="cost"]', 'strong', 'b',
];
function extractText(el, sels) {
    for (const s of sels) {
        const e = el.querySelector(s);
        if (e) {
            const t = (e.innerText || e.textContent || e.getAttribute('title') || '').trim();
            if (t) return t;
        }
    }
    return (el.innerText || el.textContent || '').trim().split('\n')[0].trim();
}
function extractPrice(el) {
    for (const s of PRICE_SELS) {
        const e = el.querySelector(s);
        if (e) {
            const txt = (e.innerText || e.textContent || '').replace(/,/g, '');
            const m   = txt.match(/\b(\d{4,6})\b/);
            if (m) { const v = parseInt(m[1]); if (v >= MIN && v <= MAX) return v; }
        }
    }
    const raw = (el.innerText || el.textContent || '').replace(/,/g, '');
    const matches = raw.match(/\b(\d{4,6})\b/g) || [];
    for (const m of matches) { const v = parseInt(m); if (v >= MIN && v <= MAX) return v; }
    return null;
}
function extractUrl(el) {
    const a = el.querySelector('a[href]') || el.closest('a[href]');
    if (a) { const href = a.getAttribute('href') || ''; return href.startsWith('http') ? href : 'https://priceoye.pk' + href; }
    return null;
}
const IMG_ATTRS = ['src', 'data-src', 'data-lazy-src', 'data-original', 'data-echo', 'data-lazy', 'srcset', 'data-srcset'];
function extractImage(el) {
    const img = el.querySelector('img');
    if (!img) return null;
    for (const attr of IMG_ATTRS) {
        let val = img.getAttribute(attr);
        if (!val) continue;
        if (attr.includes('srcset')) val = val.split(',')[0].trim().split(' ')[0];
        if (!val || val.startsWith('data:')) continue;
        return val.startsWith('http') ? val : (val.startsWith('//') ? 'https:' + val : 'https://priceoye.pk' + val);
    }
    return null;
}
let found = false;
for (const sel of CARD_SELS) {
    const cards = document.querySelectorAll(sel);
    if (cards.length >= 3) {
        found = true;
        cards.forEach(card => {
            if (results.length >= 200) return;
            const name  = extractText(card, NAME_SELS);
            const price = extractPrice(card);
            const url   = extractUrl(card);
            const image = extractImage(card);
            if (name && name.length >= 5 && name.length <= 150 && price) {
                const key = name.toLowerCase();
                if (!seen.has(key)) { seen.add(key); results.push({ name, price, url: url || '', image: image || '' }); }
            }
        });
        if (results.length > 0) break;
    }
}
if (!found || results.length < 5) {
    document.querySelectorAll('a[href*="priceoye"]').forEach(a => {
        if (results.length >= 200) return;
        const name  = (a.getAttribute('title') || a.innerText || '').trim();
        const container = a.closest('li, div, article') || a;
        const price = extractPrice(container);
        const url   = a.href;
        const image = extractImage(container);
        if (name && name.length >= 5 && name.length <= 150 && price) {
            const key = name.toLowerCase();
            if (!seen.has(key)) { seen.add(key); results.push({ name, price, url, image: image || '' }); }
        }
    });
}
return results;
"""


# ── ENHANCED ENGINES ────────────────────────────────────────────────────────

def engine_shopify_json_enhanced(key, cfg):
    """Shopify /products.json API with pagination to get ALL products."""
    base = cfg["base_url"]
    products = []
    
    api_url = f"{base}/products.json?limit=250"
    page = 1
    
    safe(f"[{key}] Fetching all products via Shopify API")
    
    while True:
        try:
            url = f"{api_url}&page={page}" if page > 1 else api_url
            r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=30)
            if r.status_code != 200:
                break
                
            data = r.json()
            prods = data.get("products", [])
            if not prods:
                break
                
            for prod in prods:
                name = prod.get("title", "").strip()
                handle = prod.get("handle", "")
                prices = [float(v["price"]) for v in prod.get("variants", []) if v.get("price")]
                price = min(prices) if prices else None
                if not name or not price:
                    continue
                    
                image_url = ""
                if prod.get("images"):
                    image_url = prod["images"][0].get("src", "")
                if not image_url and prod.get("variants"):
                    for v in prod.get("variants", []):
                        if v.get("image_id"):
                            for img in prod.get("images", []):
                                if img.get("id") == v.get("image_id"):
                                    image_url = img.get("src", "")
                                    break
                        if v.get("image") and v["image"].get("src"):
                            image_url = v["image"].get("src")
                            break
                
                products.append({
                    "name": name, 
                    "price": price,
                    "url": f"{base}/products/{handle}",
                    "image_url": image_url,
                    "category": prod.get("product_type", ""),
                })
            
            safe(f"[{key}] Page {page}: {len(prods)} products")
            page += 1
            
            if len(prods) < 250:
                break
                
        except Exception as e:
            note_issue(key, f"Shopify API error on page {page}: {e}")
            break
    
    if products:
        safe(f"[{key}] Total: {len(products)} products from API")
        return products
    
    safe(f"[{key}] API failed — falling back to DOM scraping")
    return engine_dom_scrape_all(key, cfg)


def engine_dom_scrape_all(key, cfg):
    """Enhanced DOM scraping to get ALL products from paginated listings."""
    base = cfg["base_url"]
    products = []
    seen = set()
    
    start_url = cfg.get("collection_url", base + "/collections/all")
    
    driver = None
    try:
        driver = _get_driver()
        driver.get(start_url)
        time.sleep(2)
        
        _human_scroll(driver, pause=0.3, max_steps=20)
        clicks = _click_load_more(driver, max_clicks=30)
        safe(f"[{key}] Clicked load more {clicks} times")
        _human_scroll(driver, pause=0.3, max_steps=10)
        
        fb = cfg.get("dom_fallback", {})
        product_selectors = fb.get("product_selectors", [
            "div[data-product-id]", ".product-item", ".product-card",
            "[class*='product-item']", "[class*='ProductCard']",
            "li[class*='product']", ".grid-product", ".product",
            "[data-product]"
        ])
        
        all_cards = []
        for sel in product_selectors:
            cards = driver.find_elements(By.CSS_SELECTOR, sel)
            if cards:
                all_cards.extend(cards)
                safe(f"[{key}] Found {len(cards)} cards with selector: {sel}")
        
        if not all_cards:
            price_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'Rs.') or contains(text(), 'NPR') or contains(text(), 'रू')]")
            for elem in price_elements:
                try:
                    parent = elem.find_element(By.XPATH, "./ancestor::div[position()<=4]")
                    all_cards.append(parent)
                except:
                    pass
        
        safe(f"[{key}] Total cards found: {len(all_cards)}")
        
        name_selectors = fb.get("name_selectors", ["h2", "h3", "h4", "[class*='product-title']", "[class*='title']", "[class*='name']"])
        price_selectors = fb.get("price_selectors", ["[class*='price__current']", "[class*='price__sale']", ".price-item--sale", ".price-item--regular", "[class*='price']", ".money"])
        image_selectors = fb.get("image_selectors", ["img", ".product-image img", "[class*='image'] img"])
        
        for card in all_cards[:500]:
            try:
                html = BeautifulSoup(card.get_attribute("outerHTML"), "html.parser")
                name = _extract_by_selector_chain(html, name_selectors)
                if not name or len(name) < 3:
                    continue
                    
                price_text = _extract_by_selector_chain(html, price_selectors) or html.get_text()
                price = parse_price(price_text, *cfg["price_range"])
                if not price:
                    continue
                    
                url = ""
                try:
                    a = card.find_element(By.CSS_SELECTOR, "a[href]")
                    url = a.get_attribute("href") or ""
                except:
                    pass
                
                image = _extract_image(html, image_selectors, base)
                
                key_name = name.lower().strip()
                if key_name not in seen:
                    seen.add(key_name)
                    products.append({
                        "name": name,
                        "price": price,
                        "url": url,
                        "image_url": image,
                        "category": _extract_by_selector_chain(html, ["[class*='category']", "[class*='type']"])
                    })
            except Exception as e:
                continue
                
        safe(f"[{key}] Extracted {len(products)} products from DOM")
        
    except Exception as e:
        note_issue(key, f"DOM/Selenium fallback error: {e}")
    finally:
        if driver:
            driver.quit()
            
    return products


def engine_brand_crawl_enhanced(key, cfg):
    """Iterate all brands and ALL product pages."""
    base = cfg["base_url"]
    session = requests.Session()
    headers = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9", "Referer": base + "/"}
    
    skip_re = re.compile(cfg.get("skip_name_regex", r"$^"), re.IGNORECASE)
    products, seen = [], set()
    
    for brand_name, slug in cfg["brands"]:
        page = 1
        brand_products = 0
        
        while True:
            if page == 1:
                url = base + cfg["brand_path_template"].format(slug=slug)
            else:
                url = base + cfg["brand_path_template"].format(slug=slug) + f"/page-{page}"
            
            try:
                safe(f"[{key}] {brand_name} page {page}: {url}")
                r = session.get(url, headers=headers, timeout=25)
                if r.status_code != 200:
                    if page == 1:
                        safe(f"[{key}] {brand_name}: HTTP {r.status_code}")
                    break
                    
                soup = BeautifulSoup(r.text, "html.parser")
                containers = []
                for sel in cfg["container_selectors"]:
                    containers = soup.select(sel)
                    if containers:
                        break
                
                if not containers:
                    containers = [li for li in soup.select("li, div.product, div.item") 
                                 if li.get_text(strip=True) and re.search(r"[\d,]{4,}", li.get_text())]
                
                if not containers:
                    break
                
                count = 0
                for item in containers:
                    name = _extract_by_selector_chain(item, cfg["name_selectors"])
                    if not name or len(name) < 5 or skip_re.search(name):
                        continue
                    price_text = _extract_by_selector_chain(item, cfg["price_selectors"]) or item.get_text()
                    price = parse_price(price_text, *cfg["price_range"])
                    if not price:
                        continue
                    href = make_absolute(base, _extract_by_selector_chain(item, ["a"], get_attr="href"))
                    image = _extract_image(item, cfg.get("image_selectors"), base)
                    key_ = name.lower().strip()
                    if key_ not in seen:
                        seen.add(key_)
                        products.append({
                            "name": name, 
                            "price": price, 
                            "url": href,
                            "category": brand_name, 
                            "image_url": image
                        })
                        count += 1
                
                brand_products += count
                safe(f"[{key}] {brand_name} page {page}: {count} phones")
                
                next_link = soup.select_one("a.next, a[rel='next'], .pagination-next a, .next-page")
                if not next_link:
                    break
                    
                page += 1
                time.sleep(cfg.get("request_delay", 0.3))
                
            except Exception as e:
                note_issue(key, f"{brand_name} page {page}: {e}")
                break
                
        safe(f"[{key}] {brand_name}: {brand_products} total products")
        
    return products


def engine_selenium_js_enhanced(key, cfg):
    """Enhanced Selenium JS extraction with pagination support."""
    products = []
    seen = set()
    driver = None
    
    try:
        driver = _get_driver()
        
        search_terms = cfg.get("search_terms", ["phone", "mobile", "smartphone"])
        base_url = cfg["base_url"]
        
        for term in search_terms:
            search_url = cfg["target_url"] + f"&search={quote_plus(term)}" if "?" in cfg["target_url"] else cfg["target_url"] + f"?search={quote_plus(term)}"
            
            safe(f"[{key}] Searching: {term}")
            driver.get(search_url)
            time.sleep(1.5)
            
            _human_scroll(driver, pause=0.3, max_steps=15)
            _click_load_more(driver, max_clicks=15)
            
            raw = driver.execute_script(cfg["js_extractor"]) or []
            safe(f"[{key}] '{term}' JS extractor returned {len(raw)} items")
            
            for item in raw:
                name = str(item.get("name", "")).strip()
                price = item.get("price")
                if name and price:
                    key_name = name.lower().strip()
                    if key_name not in seen:
                        seen.add(key_name)
                        products.append({
                            "name": name, 
                            "price": price, 
                            "url": item.get("url", ""),
                            "image_url": item.get("image") or None,
                            "category": term.title()
                        })
            
            if len(raw) < 5 and cfg.get("dom_fallback"):
                fb = cfg["dom_fallback"]
                cards = []
                for sel in fb["product_selectors"]:
                    cards = driver.find_elements(By.CSS_SELECTOR, sel)
                    if len(cards) >= 3:
                        break
                
                for card in cards[:100]:
                    try:
                        html = BeautifulSoup(card.get_attribute("outerHTML"), "html.parser")
                        name = _extract_by_selector_chain(html, fb["name_selectors"])
                        if not name:
                            continue
                        price_text = _extract_by_selector_chain(html, fb["price_selectors"]) or card.text
                        price = parse_price(price_text, *cfg["price_range"])
                        if not name or not price:
                            continue
                        key_name = name.lower().strip()
                        if key_name in seen:
                            continue
                        seen.add(key_name)
                        href = ""
                        try:
                            a = card.find_element(By.CSS_SELECTOR, "a[href]")
                            href = a.get_attribute("href") or ""
                        except:
                            pass
                        image = _extract_image(html, cfg.get("image_selectors"), base_url)
                        products.append({
                            "name": name, 
                            "price": price, 
                            "url": href, 
                            "image_url": image,
                            "category": term.title()
                        })
                    except:
                        continue
        
        safe(f"[{key}] Total: {len(products)} products")
        
    except Exception as e:
        note_issue(key, f"Selenium engine failed to run: {e}")
    finally:
        if driver:
            driver.quit()
            
    return products


def _make_crawl_session():
    """Create a requests session with proper headers for crawling."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    })
    return session


def engine_bfs_crawl_enhanced(key, cfg):
    """Enhanced BFS crawl with better product detection."""
    base_url = cfg["base_url"]
    session = _make_crawl_session()

    # Probe the root page first so a total block (Cloudflare challenge, DNS,
    # SSL, timeout) is reported clearly instead of silently crawling nothing.
    try:
        probe = session.get(base_url, timeout=15)
        if probe.status_code != 200:
            note_issue(key, f"root page returned HTTP {probe.status_code} "
                             f"(Cloudflare/anti-bot block is likely)")
        elif "cf-challenge" in probe.text.lower() or "checking your browser" in probe.text.lower():
            note_issue(key, "root page served a Cloudflare JS challenge page instead of real HTML")
    except Exception as e:
        note_issue(key, f"could not reach root page {base_url}: {e}")

    visited = {base_url}
    queue = deque([base_url])
    products = []
    seen = set()
    page_count = 0
    max_pages = cfg.get("max_pages", 200)
    batch_size = cfg.get("crawl_workers", 10)

    def is_product_page(url):
        u = url.lower()
        return (any(k in u for k in cfg["product_url_keywords"]) or 
                bool(re.search(r"/\d+$", url)) or
                any(p in u for p in ["/product/", "/item/", "/p-", "/p/", "/detail/"]))

    def is_category_page(url):
        u = url.lower()
        return any(re.search(p, u) for p in cfg["category_url_patterns"])

    def should_skip(url):
        u = url.lower()
        if any(p in u for p in cfg["skip_url_patterns"]):
            return True
        return any(u.endswith(ext) for ext in (".jpg", ".png", ".pdf", ".zip", ".mp4", ".css", ".js"))

    def fetch_one(url):
        try:
            r = session.get(url, timeout=15)
            r.raise_for_status()
            r.encoding = "utf-8"
            return url, r.text
        except Exception:
            return url, None

    with ThreadPoolExecutor(max_workers=batch_size) as pool:
        while queue and page_count < max_pages:
            batch = []
            while queue and len(batch) < batch_size and page_count + len(batch) < max_pages:
                url = queue.popleft()
                if should_skip(url):
                    continue
                batch.append(url)
            if not batch:
                break

            futures = [pool.submit(fetch_one, u) for u in batch]
            for fut in as_completed(futures):
                url, html = fut.result()
                page_count += 1
                if html is None:
                    continue

                soup = BeautifulSoup(html, "html.parser")

                if is_product_page(url) or any(sel in url for sel in ["/product/", "/item/", "/p-"]):
                    name = _extract_by_selector_chain(soup, cfg["name_selectors"])
                    price = None
                    price_text = _extract_by_selector_chain(soup, cfg["price_selectors"])
                    if price_text:
                        price = parse_price(price_text, *cfg["price_range"])
                    
                    if not price:
                        page_text = soup.get_text()
                        for pattern in cfg["price_regex_patterns"]:
                            m = re.search(pattern, page_text)
                            if m:
                                price = parse_price(m.group(0), *cfg["price_range"])
                                if price:
                                    break
                    
                    if name and price:
                        if len(name) < 5:
                            h1 = soup.find("h1")
                            if h1:
                                name = h1.get_text(strip=True)
                        
                        category = _extract_by_selector_chain(soup, cfg["category_selectors"])
                        image = _extract_image(soup, cfg.get("image_selectors"), base_url)
                        
                        key_name = name.lower().strip()
                        if key_name not in seen:
                            seen.add(key_name)
                            products.append({
                                "name": name, 
                                "price": price, 
                                "url": url,
                                "category": category or None,
                                "image_url": image,
                            })

                for a in soup.find_all("a", href=True):
                    link = urljoin(url, a["href"])
                    link = urlparse(link)._replace(fragment="").geturl()
                    if base_url in link and link not in visited:
                        visited.add(link)
                        if is_product_page(link) or is_category_page(link):
                            queue.appendleft(link)
                        else:
                            queue.append(link)

            time.sleep(cfg.get("crawl_delay", 0.1))

    safe(f"[{key}] crawled {page_count} pages -> {len(products)} products")
    if page_count > 0 and not products:
        note_issue(key, f"crawled {page_count} pages but matched 0 products — name/price selectors likely no longer match the site's markup")
    return products


def engine_choicemandu(key, cfg):
    """Dedicated engine for Choicemandu.com.np with pagination using Selenium."""
    base = cfg["base_url"]
    products = []
    seen = set()
    
    categories = cfg.get("categories", [
        "/mobile-phones-price-in-nepal",
        "/mobile-accessories",
        "/laptop",
        "/smart-watch",
        "/audio",
        "/gaming",
        "/tv",
        "/camera"
    ])
    
    driver = None
    
    try:
        driver = _get_driver()
        
        for category in categories:
            page = 1
            category_products = 0
            max_pages = 19
            
            while page <= max_pages:
                if page == 1:
                    url = f"{base}{category}"
                else:
                    if "?" in category:
                        url = f"{base}{category}&page={page}"
                    else:
                        url = f"{base}{category}?page={page}"
                
                try:
                    safe(f"[{key}] Category {category} page {page}: {url}")
                    driver.get(url)
                    time.sleep(2)
                    
                    try:
                        WebDriverWait(driver, 15).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, "div.product-layout, .product-item, .product-card"))
                        )
                    except:
                        safe(f"[{key}] No products found on page {page}, might be last page")
                        break
                    
                    _human_scroll(driver, pause=0.5, max_steps=15)
                    
                    product_selectors = [
                        "div.product-layout",
                        ".product-item", 
                        ".product-card",
                        ".product",
                        "[class*='product-grid'] > div",
                        ".products-grid > div",
                        ".product-list > div"
                    ]
                    
                    cards = []
                    for selector in product_selectors:
                        cards = driver.find_elements(By.CSS_SELECTOR, selector)
                        if cards and len(cards) > 2:
                            safe(f"[{key}] Found {len(cards)} cards with selector: {selector}")
                            break
                    
                    if not cards:
                        safe(f"[{key}] No product cards found on page {page}")
                        break
                    
                    page_products = 0
                    for card in cards:
                        try:
                            name_selectors = ["h4 a", "h2 a", "h3 a", ".product-title", ".product-name", "[class*='title'] a", "[class*='name'] a"]
                            name = ""
                            for sel in name_selectors:
                                try:
                                    elem = card.find_element(By.CSS_SELECTOR, sel)
                                    name = elem.text.strip()
                                    if name:
                                        break
                                except:
                                    continue
                            
                            if not name:
                                try:
                                    links = card.find_elements(By.CSS_SELECTOR, "a[href]")
                                    for link in links:
                                        text = link.text.strip()
                                        if text and len(text) > 3:
                                            name = text
                                            break
                                except:
                                    pass
                            
                            if not name or len(name) < 3:
                                continue
                            
                            price_selectors = [".price", ".product-price", ".amount", "[class*='price']", "[class*='Price']"]
                            price_text = ""
                            for sel in price_selectors:
                                try:
                                    elem = card.find_element(By.CSS_SELECTOR, sel)
                                    price_text = elem.text.strip()
                                    if price_text:
                                        break
                                except:
                                    continue
                            
                            if not price_text:
                                price_text = card.text
                            
                            price = parse_price(price_text, *cfg["price_range"])
                            
                            if not price:
                                continue
                            
                            url_link = ""
                            try:
                                a = card.find_element(By.CSS_SELECTOR, "a[href]")
                                url_link = a.get_attribute("href") or ""
                                if not url_link:
                                    links = card.find_elements(By.CSS_SELECTOR, "a[href]")
                                    for link in links:
                                        href = link.get_attribute("href")
                                        if href and "/" in href:
                                            url_link = href
                                            break
                            except:
                                pass
                            
                            image = _extract_image(card, cfg.get("image_selectors"), base)
                            
                            key_name = name.lower().strip()
                            if key_name not in seen:
                                seen.add(key_name)
                                products.append({
                                    "name": name[:200],
                                    "price": price,
                                    "url": url_link,
                                    "image_url": image,
                                    "category": category.strip("/").replace("-", " ").title()
                                })
                                page_products += 1
                                category_products += 1
                                
                        except Exception as e:
                            continue
                    
                    safe(f"[{key}] {category} page {page}: {page_products} products (total {len(products)})")
                    
                    if page_products == 0:
                        break
                    
                    try:
                        next_selectors = [
                            "a.next", 
                            "a[rel='next']", 
                            ".pagination-next a",
                            ".next-page", 
                            ".pagination .next",
                            "[aria-label='Next']"
                        ]
                        next_found = False
                        for sel in next_selectors:
                            try:
                                next_btn = driver.find_element(By.CSS_SELECTOR, sel)
                                if next_btn.is_displayed() and next_btn.is_enabled():
                                    next_found = True
                                    break
                            except:
                                continue
                        
                        if not next_found and page_products > 0:
                            try:
                                pagination = driver.find_elements(By.CSS_SELECTOR, ".pagination a, .page-numbers a")
                                if pagination:
                                    page_numbers = []
                                    for p in pagination:
                                        try:
                                            num = int(p.text.strip())
                                            page_numbers.append(num)
                                        except:
                                            pass
                                    if page_numbers and max(page_numbers) > page:
                                        next_found = True
                            except:
                                pass
                            
                            if not next_found:
                                safe(f"[{key}] No next page found for {category}, stopping")
                                break
                                
                    except Exception as e:
                        safe(f"[{key}] Error checking next page: {e}")
                        if page_products > 0 and page < max_pages:
                            safe(f"[{key}] Continuing to next page anyway")
                        else:
                            break
                    
                    page += 1
                    time.sleep(0.5)
                    
                except Exception as e:
                    safe(f"[{key}] {category} page {page}: error {e}")
                    break
            
            safe(f"[{key}] {category}: {category_products} total products")
            
    except Exception as e:
        note_issue(key, f"Selenium engine failed to run: {e}")
    finally:
        if driver:
            driver.quit()
    
    safe(f"[{key}] Total: {len(products)} products from Choicemandu")
    return products


def engine_dealayo(key, cfg):
    """
    Dedicated engine for Dealayo.com using Selenium to scrape all phone listings.
    This replaces the fatafatsewa engine with a fully working Dealayo scraper.
    """
    base = cfg["base_url"]
    products = []
    seen = set()
    
    driver = None
    
    try:
        safe(f"[{key}] Starting Dealayo scraper...")
        driver = _get_driver()
        
        # Load the mobile page
        safe(f"[{key}] Loading Dealayo mobile page...")
        driver.get("https://dealayo.com/mobile.html")
        time.sleep(3)
        
        # Handle popup if present
        try:
            close_btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button.close, .modal-header .close, [data-dismiss='modal']"))
            )
            close_btn.click()
            safe(f"[{key}] ✓ Popup closed")
            time.sleep(1)
        except:
            safe(f"[{key}] No popup found")
        
        page_num = 1
        max_pages = cfg.get("max_pages_per_term", 15)
        
        while page_num <= max_pages:
            safe(f"[{key}] {'='*50}")
            safe(f"[{key}] Page {page_num}")
            safe(f"[{key}] {'='*50}")
            
            # Wait for products to load
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".product-item, .item, .product-card"))
                )
            except:
                safe(f"[{key}] No products found on page {page_num}")
                break
            
            # Find all products using multiple selectors
            product_selectors = [
                ".product-item",
                ".item", 
                ".product-card",
                ".product-grid-item",
                "li.product-item",
                "[class*='product-item']",
                "[class*='ProductCard']"
            ]
            
            products_on_page = []
            for selector in product_selectors:
                cards = driver.find_elements(By.CSS_SELECTOR, selector)
                if cards and len(cards) > 0:
                    products_on_page = cards
                    safe(f"[{key}] Found {len(cards)} products with selector: {selector}")
                    break
            
            if not products_on_page:
                safe(f"[{key}] No product cards found on page {page_num}")
                break
            
            page_products_count = 0
            
            for idx, product in enumerate(products_on_page, 1):
                try:
                    # Extract phone name
                    name = None
                    name_selectors = [
                        ".product-name",
                        ".product-title",
                        "h2",
                        "h3",
                        ".name",
                        ".product-name a",
                        "h2 a",
                        "h3 a"
                    ]
                    
                    for selector in name_selectors:
                        try:
                            name_elem = product.find_element(By.CSS_SELECTOR, selector)
                            name = name_elem.text.strip()
                            if name:
                                break
                        except:
                            continue
                    
                    # If name still not found, try any link
                    if not name:
                        try:
                            links = product.find_elements(By.TAG_NAME, "a")
                            for link in links:
                                text = link.text.strip()
                                if text and len(text) > 3:
                                    name = text
                                    break
                        except:
                            pass
                    
                    if not name or len(name) < 3:
                        continue
                    
                    # Extract price
                    price = None
                    price_selectors = [
                        ".price",
                        ".product-price",
                        ".special-price",
                        ".regular-price",
                        ".special-price .price",
                        ".price-box .price",
                        "[class*='price']",
                        "span.price"
                    ]
                    
                    for selector in price_selectors:
                        try:
                            price_elem = product.find_element(By.CSS_SELECTOR, selector)
                            price_text = price_elem.text.strip()
                            if price_text:
                                price = parse_price(price_text, *cfg["price_range"])
                                if price:
                                    break
                        except:
                            continue
                    
                    if not price:
                        # Try to find any price in the card text
                        card_text = product.text
                        price = parse_price(card_text, *cfg["price_range"])
                    
                    if not price:
                        continue
                    
                    # Extract URL
                    url_link = ""
                    try:
                        a_tag = product.find_element(By.CSS_SELECTOR, "a[href]")
                        if a_tag:
                            url_link = a_tag.get_attribute("href") or ""
                    except:
                        pass
                    
                    # Extract image
                    image = None
                    try:
                        img = product.find_element(By.TAG_NAME, "img")
                        if img:
                            for attr in ["src", "data-src", "data-original"]:
                                if img.get_attribute(attr):
                                    image = make_absolute(base, img.get_attribute(attr))
                                    break
                    except:
                        pass
                    
                    # Clean up name
                    name = re.sub(r"\s+", " ", name).strip()
                    name = re.sub(r"(?i)add to cart|quick view|view details", "", name).strip()
                    
                    key_name = name.lower().strip()
                    if key_name not in seen:
                        seen.add(key_name)
                        products.append({
                            "name": name[:200],
                            "price": price,
                            "url": url_link or None,
                            "image_url": image or None,
                            "category": "Mobile Phones",
                        })
                        page_products_count += 1
                        
                except Exception as e:
                    continue
            
            safe(f"[{key}] Page {page_num}: {page_products_count} products (total {len(products)})")
            
            if page_products_count == 0:
                safe(f"[{key}] No products on page {page_num}, stopping")
                break
            
            # Try to go to next page
            try:
                next_selectors = [
                    ".next",
                    ".pages-item-next", 
                    ".action.next",
                    "a[title='Next']",
                    ".pagination .next",
                    "a.next"
                ]
                
                next_found = False
                for selector in next_selectors:
                    try:
                        next_btn = driver.find_element(By.CSS_SELECTOR, selector)
                        if next_btn and next_btn.is_enabled():
                            # Check if it's disabled
                            class_attr = next_btn.get_attribute("class") or ""
                            if "disabled" not in class_attr and "inactive" not in class_attr:
                                next_found = True
                                # Scroll to button and click
                                driver.execute_script("arguments[0].scrollIntoView(true);", next_btn)
                                time.sleep(0.5)
                                next_btn.click()
                                safe(f"[{key}] Going to next page...")
                                time.sleep(2)
                                page_num += 1
                                break
                    except:
                        continue
                
                if not next_found:
                    safe(f"[{key}] ✓ Reached last page or no next button found")
                    break
                    
            except Exception as e:
                safe(f"[{key}] Pagination error: {e}")
                break
                
    except Exception as e:
        note_issue(key, f"Dealayo scraper error: {e}")
        safe(f"[{key}] Error: {e}")
    
    finally:
        if driver:
            driver.quit()
            safe(f"[{key}] Driver closed")
    
    safe(f"[{key}] Total: {len(products)} products from Dealayo")
    return products


def engine_table_heading_enhanced(key, cfg):
    """Enhanced table/heading parser that handles multiple pages."""
    base = cfg["base_url"]
    products = []
    seen = set()
    
    for page in range(1, cfg.get("max_pages", 5) + 1):
        url = cfg["target_url"] + f"/page/{page}/" if page > 1 else cfg["target_url"]
        
        try:
            safe(f"[{key}] Fetching page {page}: {url}")
            r = fetch_with_retry(url, headers=rotating_headers())
            if r.status_code != 200:
                note_issue(key, f"page {page}: HTTP {r.status_code}")
                continue
            r.encoding = "utf-8"
            soup = BeautifulSoup(r.content, "html.parser")
        except Exception as e:
            note_issue(key, f"fetch error page {page}: {e}")
            continue

        current_brand = "Unknown"
        skip_headings = [s.lower() for s in cfg["skip_heading_keywords"]]
        skip_values = [s.lower() for s in cfg["header_row_skip_values"]]
        page_products = 0

        for element in soup.find_all(cfg["heading_tags"] + ["table", ".product-table", ".price-table"]):
            if element.name in cfg["heading_tags"]:
                text = element.get_text(strip=True)
                if not any(skip in text.lower() for skip in skip_headings):
                    current_brand = text
                continue

            for row in element.find_all("tr"):
                cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"]) if c.get_text(strip=True)]
                if len(cells) < 2:
                    continue
                    
                price_idx, price_val = None, None
                for i, text in enumerate(cells):
                    m = re.search(r"[\d,]+\.?\d*", text)
                    if m:
                        candidate = m.group().replace(",", "")
                        if len(candidate) >= 3 and float(candidate) > 100:
                            price_val, price_idx = candidate, i
                            break
                            
                if price_idx is None:
                    continue
                    
                name = cells[price_idx - 1] if price_idx > 0 else (cells[1] if len(cells) > 1 else "Unknown")
                name = re.sub(r"\s+", " ", name).strip()
                name = re.sub(r"Rs\.?[\d,]+\.?\d*", "", name).strip()
                
                if name.lower() in skip_values or not name or len(name) < 3:
                    continue
                    
                try:
                    price = float(price_val)
                except ValueError:
                    continue
                    
                if cfg["price_range"][0] <= price <= cfg["price_range"][1]:
                    key_name = name.lower().strip()
                    if key_name not in seen:
                        seen.add(key_name)
                        products.append({
                            "name": name, 
                            "price": price, 
                            "category": current_brand, 
                            "url": None,
                            "image_url": _extract_image(soup, cfg.get("image_selectors"), base)
                        })
                        page_products += 1

        safe(f"[{key}] page {page}: {page_products} products")
        
        next_link = soup.select_one("a.next, a[rel='next'], .pagination-next a")
        if not next_link:
            break

    safe(f"[{key}] total: {len(products)} products")
    return products


def engine_html_search_enhanced(key, cfg):
    """Enhanced search with multiple pages and Selenium fallback with image fixes."""
    from urllib.parse import quote_plus
    products, seen = [], set()
    skip_re = re.compile(cfg.get("skip_name_regex", r"$^"), re.IGNORECASE)

    for term in cfg["search_terms"]:
        for page in range(1, cfg.get("max_pages_per_term", 5) + 1):
            url = cfg["search_url_template"].format(query=quote_plus(term), page=page)
            try:
                r = fetch_with_retry(url, headers=rotating_headers({"Referer": cfg["base_url"] + "/"}))
                if r.status_code != 200:
                    note_issue(key, f"'{term}' p{page}: HTTP {r.status_code} (likely bot-blocked)")
                    break
            except Exception as e:
                note_issue(key, f"'{term}' p{page}: fetch failed ({e})")
                break

            soup = BeautifulSoup(r.text, "html.parser")
            cards = []
            for sel in cfg["product_selectors"]:
                cards = soup.select(sel)
                if cards:
                    break
            if not cards:
                note_issue(key, f"'{term}' p{page}: 0 cards matched product_selectors "
                                 f"(page fetched OK — content is likely rendered client-side via JS)")
                break

            found_this_page = 0
            for card in cards:
                name = _extract_by_selector_chain(card, cfg["name_selectors"])
                if not name or len(name) < 5 or skip_re.search(name):
                    continue
                price_text = _extract_by_selector_chain(card, cfg["price_selectors"]) or card.get_text()
                price = parse_price(price_text, *cfg["price_range"])
                if not price:
                    continue
                href = make_absolute(cfg["base_url"], _extract_by_selector_chain(card, ["a"], get_attr="href"))
                
                image = _extract_daraz_image(card, cfg["base_url"])
                if not image:
                    image = _extract_image(card, cfg.get("image_selectors"), cfg["base_url"])
                
                key_ = name.lower().strip()
                if key_ in seen:
                    continue
                seen.add(key_)
                products.append({
                    "name": name, 
                    "price": price, 
                    "url": href,
                    "image_url": image, 
                    "category": term.title()
                })
                found_this_page += 1
            safe(f"[{key}] '{term}' p{page}: {found_this_page} products")
            if found_this_page == 0:
                break
            time.sleep(cfg.get("request_delay", 0.5))

    if len(products) < 10 and cfg.get("dom_fallback"):
        safe(f"[{key}] plain HTML gave {len(products)} results — falling back to Selenium")
        fb = cfg["dom_fallback"]
        driver = None
        try:
            driver = _get_driver()
            for term in cfg["search_terms"][:3]:
                driver.get(cfg["search_url_template"].format(query=quote_plus(term), page=1))
                time.sleep(1.2)
                _human_scroll(driver)
                cards = []
                for sel in fb["product_selectors"]:
                    cards = driver.find_elements(By.CSS_SELECTOR, sel)
                    if len(cards) >= 3:
                        break
                for card in cards[:150]:
                    try:
                        html = BeautifulSoup(card.get_attribute("outerHTML"), "html.parser")
                        name = _extract_by_selector_chain(html, fb["name_selectors"])
                        price_text = _extract_by_selector_chain(html, fb["price_selectors"]) or card.text
                        price = parse_price(price_text, *cfg["price_range"])
                        if not name or not price or name.lower() in seen:
                            continue
                        seen.add(name.lower())
                        href = ""
                        try:
                            href = card.find_element(By.CSS_SELECTOR, "a[href]").get_attribute("href") or ""
                        except:
                            pass
                        image = _extract_daraz_image(card, cfg["base_url"])
                        if not image:
                            image = _extract_image(html, fb.get("image_selectors"), cfg["base_url"])
                        products.append({
                            "name": name, 
                            "price": price, 
                            "url": href,
                            "category": term.title(), 
                            "image_url": image
                        })
                    except:
                        continue
        except Exception as e:
            note_issue(key, f"Selenium fallback error: {e}")
        finally:
            if driver:
                driver.quit()
                
    return products


def engine_generic_auto_enhanced(key, cfg):
    """Enhanced config-free crawl with better pagination detection."""
    base_url = cfg["base_url"]
    start_url = cfg.get("start_url", base_url)
    price_range = cfg["price_range"]
    max_pages = cfg.get("max_pages", 50)
    render_js = cfg.get("render_js", False)

    visited = {start_url}
    queue = deque([start_url])
    products, seen = [], set()
    page_count = 0
    driver = _get_driver() if render_js else None

    try:
        while queue and page_count < max_pages:
            url = queue.popleft()
            low = url.lower()
            if any(w in low for w in _SKIP_LINK_WORDS) or low.endswith(_SKIP_LINK_EXTS):
                continue
            page_count += 1
            try:
                if render_js:
                    driver.get(url)
                    time.sleep(0.8)
                    _human_scroll(driver, pause=0.25, max_steps=12)
                    html = driver.page_source
                else:
                    r = fetch_with_retry(url, max_retries=2)
                    html = r.text
                soup = BeautifulSoup(html, "html.parser")
            except Exception as e:
                safe(f"[{key}] {url}: fetch failed ({e})")
                continue

            cards = _auto_detect_cards(soup)
            page_hits = 0
            if cards:
                for card in cards:
                    item = _auto_extract_product(card, base_url, price_range)
                    if item:
                        k = item["name"].lower().strip()
                        if k not in seen:
                            seen.add(k)
                            products.append(item)
                            page_hits += 1
            else:
                item = _auto_extract_single_product_page(soup, url, base_url, price_range)
                if item:
                    k = item["name"].lower().strip()
                    if k not in seen:
                        seen.add(k)
                        products.append(item)
                        page_hits += 1
                    
            safe(f"[{key}] {url}: {page_hits} product(s) (page {page_count}/{max_pages})")

            for pagination in ["a.next", "a[rel='next']", ".pagination-next a", ".next-page", "a.next-page"]:
                next_link = soup.select_one(pagination)
                if next_link and next_link.get("href"):
                    next_url = make_absolute(base_url, next_link["href"])
                    if next_url not in visited:
                        visited.add(next_url)
                        queue.append(next_url)

            for a in soup.find_all("a", href=True):
                link = urljoin(url, a["href"])
                link = urlparse(link)._replace(fragment="").geturl()
                if base_url in link and link not in visited and len(visited) < max_pages * 5:
                    visited.add(link)
                    queue.append(link)

            time.sleep(0.2)
    finally:
        if driver:
            driver.quit()

    safe(f"[{key}] crawled {page_count} pages -> {len(products)} products")
    return products


# ── SITE CONFIGURATION ──────────────────────────────────────────────────────

ENGINE_HEADLESS = True

SITES = {
    "brother_mart": {
        "label": "Brother Mart", "country": "Nepal", "currency": "NPR",
        "engine": "shopify_json_enhanced",
        "base_url": "https://brother-mart.com",
        "collection_url": "https://brother-mart.com/collections/all",
        "price_range": (2000, 250000),
        "dom_fallback": {
            "needs_selenium": True,
            "product_selectors": ["div[data-product-id]", ".product-item", ".product-card",
                                   "[class*='product-item']", "[class*='ProductCard']",
                                   "li[class*='product']", ".grid-product"],
            "name_selectors": ["h2", "h3", "h4", "[class*='product-title']",
                                "[class*='title']", "[class*='name']"],
            "price_selectors": ["[class*='price__current']", "[class*='price__sale']",
                                 ".price-item--sale", ".price-item--regular",
                                 "[class*='price']", ".money", "[data-price]"],
            "image_selectors": ["img", ".product-image img", "[class*='image'] img"],
        },
    },
    "sinja": {
        "label": "Sinja", "country": "Nepal", "currency": "NPR",
        "engine": "shopify_json_enhanced",
        "base_url": "https://sinja.com.np",
        "collection_url": "https://sinja.com.np/collections/all",
        "price_range": (2000, 250000),
        "dom_fallback": {
            "needs_selenium": True,
            "product_selectors": ["div[data-product-id]", ".product-item", ".product-card",
                                   "[class*='product-item']", "[class*='ProductCard']",
                                   "li[class*='product']", ".grid-product"],
            "name_selectors": ["h2", "h3", "h4", "[class*='product-title']",
                                "[class*='title']", "[class*='name']"],
            "price_selectors": ["[class*='price__current']", "[class*='price__sale']",
                                 ".price-item--sale", ".price-item--regular",
                                 "[class*='price']", ".money", "[data-price]"],
            "image_selectors": ["img", ".product-image img", "[class*='image'] img"],
        },
    },
    "91mobiles": {
        "label": "91mobiles", "country": "India", "currency": "INR",
        "engine": "brand_crawl_enhanced",
        "base_url": "https://www.91mobiles.com",
        "brand_path_template": "/list-of-phones/{slug}",
        "price_range": (3000, 300000),
        "request_delay": 0.3,
        "brands": [
            ("Samsung", "samsung-mobile-price-list-in-india"),
            ("Xiaomi", "xiaomi-mobile-price-list-in-india"),
            ("Realme", "realme-mobile-price-list-in-india"),
            ("Oppo", "oppo-mobile-price-list-in-india"),
            ("Vivo", "vivo-mobile-price-list-in-india"),
            ("OnePlus", "oneplus-mobile-price-list-in-india"),
            ("Poco", "poco-mobile-price-list-in-india"),
            ("Motorola", "motorola-mobile-price-list-in-india"),
            ("Nokia", "nokia-mobile-price-list-in-india"),
            ("Infinix", "infinix-mobile-price-list-in-india"),
            ("Itel", "itel-mobile-price-list-in-india"),
            ("Tecno", "tecno-mobile-price-list-in-india"),
            ("Apple", "apple-mobile-price-list-in-india"),
            ("Honor", "honor-mobile-price-list-in-india"),
            ("Nothing", "nothing-mobile-price-list-in-india"),
            ("Google", "google-mobile-price-list-in-india"),
            ("Lava", "lava-mobile-price-list-in-india"),
            ("iQOO", "iqoo-mobile-price-list-in-india"),
        ],
        "container_selectors": ["li[data-model-id]", ".productCard", ".phoneList li",
                                 "ul.productList li", "[class*='productList'] li",
                                 "[class*='listingCard']"],
        "name_selectors": ["h3", "h2", "h4", "[class*='name']", "[class*='title']", "[class*='Name']", "a"],
        "price_selectors": ["[class*='priceValue']", "[class*='price__current']",
                             "[class*='selling']", "[class*='offer']", "[class*='price']", ".price"],
        "image_selectors": ["img", ".product-image img", "[class*='image'] img"],
        "skip_name_regex": r'\b(accessory|accessories|charger|earphone|cable|case|cover|screen guard|protector|adapter)\b',
    },
    "priceoye": {
        "label": "PriceOye", "country": "Pakistan", "currency": "PKR",
        "engine": "selenium_js_enhanced",
        "base_url": "https://priceoye.pk",
        "target_url": "https://priceoye.pk/search",
        "search_terms": ["phone", "mobile", "smartphone", "handset"],
        "price_range": (5000, 300000),
        "js_extractor": PRICEOYE_EXTRACT_JS,
        "dom_fallback": {
            "product_selectors": [".productCard", ".product-item", ".item-card",
                                   "[class*='ProductCard']", "[class*='product-card']",
                                   ".pListingsSection li", ".search-results li",
                                   "li[class*='item']", "[class*='listing'] li"],
            "name_selectors": ["h3", "h2", "h4", ".productTitle", "[class*='title']",
                                "[class*='name']", "a[title]"],
            "price_selectors": [".price", "[class*='price']", "[class*='Price']",
                                 "[class*='amount']", "strong", "b"],
            "image_selectors": ["img", ".product-image img", "[class*='image'] img"],
        },
    },
    "gadgetbytenepal": {
        "label": "GadgetByte Nepal", "country": "Nepal", "currency": "NPR",
        "engine": "table_heading_enhanced",
        "base_url": "https://www.gadgetbytenepal.com",
        "target_url": "https://www.gadgetbytenepal.com/category/mobile-price-in-nepal/",
        "price_range": (100, 600000),
        "max_pages": 10,
        "heading_tags": ["h2", "h3", "h4"],
        "skip_heading_keywords": ["price", "overview", "trend", "conclusion", "buy"],
        "header_row_skip_values": ["model", "product", "price", "best buying price"],
        "image_selectors": ["img", ".post-image img", ".product-image img"],
    },
    "daraz": {
        "label": "Daraz", "country": "Nepal", "currency": "NPR",
        "engine": "html_search_enhanced",
        "base_url": "https://www.daraz.com.np",
        "search_url_template": "https://www.daraz.com.np/catalog/?q={query}&page={page}",
        "search_terms": ["mobile phone", "laptop", "smartwatch", "earbuds", "tablet", "accessories"],
        "max_pages_per_term": 5,
        "price_range": (500, 500000),
        "request_delay": 0.5,
        "product_selectors": ["[data-qa-locator='product-item']", ".gridItem--Yd0sa",
                               ".product-card", "[class*='product-item']", "[class*='ProductItem']"],
        "name_selectors": ["[title]", "a[title]", "h3", ".title", "[class*='title']"],
        "price_selectors": [".currency--GVKjl", "[class*='price']", "[class*='currency']", ".price"],
        "skip_name_regex": r"\b(voucher|gift card|sim card|accessory only)\b",
        "image_selectors": ["img", ".product-image img", "[class*='image'] img"],
        "js_extractor": None,
        "dom_fallback": {
            "needs_selenium": True,
            "product_selectors": ["[data-qa-locator='product-item']", ".product-card",
                                   "[class*='product-item']", "[class*='ProductCard']"],
            "name_selectors": ["h3", "a[title]", "[class*='title']", "[class*='name']"],
            "price_selectors": ["[class*='price']", "[class*='currency']", ".price"],
            "image_selectors": ["img", ".product-image img", "[class*='image'] img"],
        },
    },
    "choicemandu": {
        "label": "Choicemandu", 
        "country": "Nepal", 
        "currency": "NPR",
        "engine": "choicemandu",
        "base_url": "https://choicemandu.com",
        "price_range": (500, 300000),
        "categories": [
            "/mobile-phones-price-in-nepal",
            "/mobile-accessories",
            "/laptop",
            "/smart-watch",
            "/audio",
            "/gaming",
            "/tv",
            "/camera"
        ],
        "image_selectors": ["img", ".product-image img", "[class*='image'] img"],
    },
    "dealayo": {
        "label": "Dealayo",
        "country": "Nepal",
        "currency": "NPR",
        "engine": "dealayo",
        "base_url": "https://dealayo.com",
        "price_range": (500, 500000),
        "max_pages_per_term": 15,
        "request_delay": 0.5,
    },
}


# ── ENGINE REGISTRY ──────────────────────────────────────────────────────────

ENGINES = {
    "shopify_json_enhanced": engine_shopify_json_enhanced,
    "brand_crawl_enhanced": engine_brand_crawl_enhanced,
    "selenium_js_enhanced": engine_selenium_js_enhanced,
    "bfs_crawl_enhanced": engine_bfs_crawl_enhanced,
    "table_heading_enhanced": engine_table_heading_enhanced,
    "html_search_enhanced": engine_html_search_enhanced,
    "choicemandu": engine_choicemandu,
    "generic_auto_enhanced": engine_generic_auto_enhanced,
    "dealayo": engine_dealayo,
}

CONCURRENT_SAFE_ENGINES = {
    "shopify_json_enhanced", "brand_crawl_enhanced", "bfs_crawl_enhanced", 
    "table_heading_enhanced", "html_search_enhanced", "dealayo"
}


# ── ORCHESTRATION ──────────────────────────────────────────────────────────

def run_scrape(source_key: str, force: bool = False) -> dict:
    if source_key not in SITES:
        raise ValueError(f"Unknown source: {source_key}")
    cfg = SITES[source_key]
    engine_fn = ENGINES[cfg["engine"]]

    cache_minutes = cfg.get("cache_minutes")
    if cache_minutes and not force:
        stats = database.get_stats_by_source(source_key)
        last = stats.get("last_scrape") if stats else None
        if last:
            age = datetime.now(timezone.utc) - datetime.strptime(last, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if age.total_seconds() < cache_minutes * 60 and stats.get("status") == "success":
                safe(f"[{source_key}] skipped — cached ({age.total_seconds()/60:.0f}m old)")
                return {"source": source_key, "count": stats["total"], "status": "cached",
                        "message": f"Skipped, cached ({age.total_seconds()/60:.0f}m old)"}

    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    t0 = time.time()
    status, message = "success", ""
    _ENGINE_ISSUES.pop(source_key, None)

    try:
        raw = engine_fn(source_key, cfg)
    except Exception as e:
        raw = []
        status, message = "failed", str(e)[:300]
        safe(f"[{source_key}] FATAL: {e}")

    cleaned = clean_and_dedupe(raw, cfg["price_range"])
    for p in cleaned:
        p["source"] = source_key
        p["currency"] = cfg["currency"]

    issues = _pop_issues(source_key)
    if not cleaned and status == "success":
        if issues:
            status, message = "partial", f"0 products — {issues[-1]}"
        elif raw:
            status, message = "partial", (
                f"{len(raw)} items scraped but 0 survived price-range/cleanup "
                f"filter (price_range={cfg['price_range']}) — check price_range or parse_price()"
            )
        else:
            status, message = "partial", (
                "0 products extracted — no cards matched selectors and no "
                "exception was raised (site structure likely changed, or "
                "content is JS-rendered and needs the Selenium fallback)"
            )
    elif issues and status == "success":
        message = f"{len(cleaned)} products, but with issues: {issues[-1]}"

    result = database.save_products(source_key, cleaned)
    finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    database.log_scrape(
        source=source_key, status=status, products_found=len(cleaned),
        message=message or f"Scraped {len(cleaned)} products",
        duration_seconds=round(time.time() - t0, 2),
        started_at=started_at, finished_at=finished_at,
    )
    safe(f"[{source_key}] done: {len(cleaned)} products ({status})")
    return {**result, "status": status, "message": message}


def run_selected(source_keys, max_workers: int = 8) -> dict:
    """Enhanced with real-time status tracking."""
    global _current_scrape_status
    
    reset_scrape_status()
    source_keys = [k for k in source_keys if k in SITES]
    _current_scrape_status["running"] = True
    _current_scrape_status["started_at"] = datetime.now(timezone.utc).isoformat()
    _current_scrape_status["total_stores"] = len(source_keys)
    
    for key in source_keys:
        _current_scrape_status["store_status"][key] = {
            "status": "pending",
            "products": 0,
            "started_at": None,
            "finished_at": None,
            "error": None
        }
    
    results = {}
    concurrent_keys = [k for k in source_keys if SITES[k]["engine"] in CONCURRENT_SAFE_ENGINES]
    selenium_keys = [k for k in source_keys if k not in concurrent_keys]

    def scrape_with_status(key):
        try:
            update_scrape_status(key, "scraping")
            result = run_scrape(key)
            count = result.get("count", 0)
            status = result.get("status", "success")
            update_scrape_status(key, "done" if status != "failed" else "failed", count)
            return key, result
        except Exception as e:
            update_scrape_status(key, "failed", error=str(e)[:200])
            return key, {"source": key, "count": 0, "status": "failed", "message": str(e)[:300]}

    if concurrent_keys:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(concurrent_keys))) as pool:
            futures = {pool.submit(scrape_with_status, key): key for key in concurrent_keys}
            for fut in as_completed(futures):
                key, result = fut.result()
                results[key] = result

    for i, key in enumerate(selenium_keys):
        key, result = scrape_with_status(key)
        results[key] = result
        if i < len(selenium_keys) - 1:
            time.sleep(1)
    
    _current_scrape_status["running"] = False
    _current_scrape_status["current_store"] = None
    
    return results


def run_all(max_workers: int = 8) -> dict:
    """Run all sources with real-time status tracking."""
    return run_selected(list(SITES.keys()), max_workers=max_workers)


def scrape_any_url(url, max_pages=50, price_range=(50, 2_000_000), render_js=False, label=None):
    """Public entry point for scraping ANY website on demand."""
    parsed = urlparse(url if "://" in url else "https://" + url)
    if not parsed.netloc:
        raise ValueError("Provide a full URL, e.g. https://example.com")
    domain = parsed.netloc.replace("www.", "")
    source_key = "custom_" + re.sub(r"[^a-z0-9]+", "_", domain.lower()).strip("_")
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    SITES[source_key] = {
        "label": label or domain, "country": "Unknown", "currency": "NPR",
        "engine": "generic_auto_enhanced",
        "base_url": base_url,
        "start_url": parsed.geturl(),
        "price_range": price_range,
        "max_pages": max_pages,
        "render_js": render_js,
        "cache_minutes": None,
        "image_selectors": ["img", ".product-image img", "[class*='image'] img"],
    }
    return run_scrape(source_key, force=True)


if __name__ == "__main__":
    database.init_db()
    safe("=" * 70)
    safe("  HATBHAU (हट भाउ) — ENHANCED SCRAPER")
    safe("  Scraping ALL products with real-time status tracking")
    safe(f"  Sources: {', '.join(SITES.keys())}")
    safe("=" * 70)
    summary = run_all()
    safe("\n" + "=" * 70)
    safe("  SUMMARY")
    for key, res in summary.items():
        safe(f"  {SITES[key]['label']:<20}: {res['count']:>4} products [{res['status']}]")
    safe("=" * 70)