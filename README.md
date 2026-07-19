<div align="center">

# हट भाउ · HatBhau

**Nepal's price comparison engine** — scrape, compare, and track electronics prices across 7 stores in 3 countries.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Flask](https://img.shields.io/badge/Flask-2.0+-000000.svg?logo=flask)](https://flask.palletsprojects.com/)
[![Selenium](https://img.shields.io/badge/Selenium-4.0+-43B02A.svg?logo=selenium&logoColor=white)](https://www.selenium.dev/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-2dd8c3.svg)](#-contributing)

<sub>Made in Nepal 🇳🇵</sub>

</div>

<br>

<div align="center">
<table>
<tr>
<td align="center"><b>7</b><br><sub>stores</sub></td>
<td align="center"><b>3</b><br><sub>countries</sub></td>
<td align="center"><b>6</b><br><sub>scrape engines</sub></td>
<td align="center"><b>21</b><br><sub>API endpoints</sub></td>
</tr>
</table>
</div>

<br>

## 📑 Table of Contents

<details>
<summary><b>Click to expand</b></summary>

- [Overview](#-overview)
- [Quick Start](#-quick-start)
- [Technology Stack](#️-technology-stack)
- [Project Structure](#-project-structure)
- [Configuration](#-configuration)
- [Supported Stores](#-supported-stores)
- [API Reference](#-api-reference)
- [How It Works](#-how-it-works)
- [Database Schema](#-database-schema)
- [Security](#-security)
- [Troubleshooting](#-troubleshooting)
- [Contributing](#-contributing)
- [Roadmap](#-roadmap)
- [License](#-license)

</details>

<br>

## 📖 Overview

HatBhau is a comprehensive price comparison engine for Nepal that scrapes, aggregates, and displays product prices from multiple e-commerce platforms. It provides a unified interface to search, compare, and track prices across **Daraz**, **Brother Mart**, **Sinja**, **91mobiles**, **PriceOye**, **Fatafat Sewa**, and **GadgetByte Nepal**.

<table>
<tr>
<td>🏬 Multi-store scraping</td>
<td>⚡ Real-time comparison</td>
<td>📈 Price history</td>
</tr>
<tr>
<td>🔍 Smart search & filters</td>
<td>🛠️ Admin dashboard</td>
<td>🌗 Dark / light theme</td>
</tr>
<tr>
<td>📱 Responsive design</td>
<td>📤 CSV export</td>
<td>🧵 Concurrent scraping</td>
</tr>
</table>

<br>

## 🚀 Quick Start

<details open>
<summary><b>1 · Prerequisites</b></summary>
<br>

- Python 3.8 or higher
- pip (Python package manager)
- Chrome browser (for Selenium-based scraping)
- Git

</details>

<details>
<summary><b>2 · Clone & install</b></summary>
<br>

```bash
git clone https://github.com/yourusername/hatbhau.git
cd hatbhau

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

</details>

<details>
<summary><b>3 · Set environment variables</b> <sub>(optional — pick your shell)</sub></summary>
<br>

<details>
<summary>🐧 Linux / macOS</summary>

```bash
export HATBHAU_ADMIN_USER="momo"
export HATBHAU_ADMIN_PASS="momo"
export HATBHAU_SECRET_KEY="your-secret-key-here"
export HATBHAU_DEBUG="1"
```

</details>

<details>
<summary>🪟 Windows (Command Prompt)</summary>

```cmd
set HATBHAU_ADMIN_USER=momo
set HATBHAU_ADMIN_PASS=momo
set HATBHAU_SECRET_KEY=your-secret-key-here
set HATBHAU_DEBUG=1
```

</details>

<details>
<summary>🪟 Windows (PowerShell)</summary>

```powershell
$env:HATBHAU_ADMIN_USER="momo"
$env:HATBHAU_ADMIN_PASS="momo"
$env:HATBHAU_SECRET_KEY="your-secret-key-here"
$env:HATBHAU_DEBUG="1"
```

</details>
</details>

<details>
<summary><b>4 · Run</b></summary>
<br>

```bash
python app.py
```

| | |
|---|---|
| App | `http://localhost:5000` |
| Admin panel | `http://localhost:5000/admin` &nbsp; *(default: `momo` / `momo`)* |

</details>

<br>

## 🛠️ Technology Stack

| Component | Technology | Version |
|---|---|---|
| Backend | Flask | 2.0+ |
| Database | SQLite (WAL mode) | 3.x |
| Scraping | Requests, BeautifulSoup4, Selenium | Latest |
| Frontend | Bootstrap 5, Vanilla JS | 5.3.2 |
| Styling | CSS3, dark/light themes | — |
| Icons | Bootstrap Icons | 1.11.3 |
| Fonts | Inter, Noto Sans Devanagari | — |
| Web Driver | Selenium WebDriver Manager | Latest |

<br>

## 📁 Project Structure

<details>
<summary><b>Click to expand file tree</b></summary>

```
hatbhau/
├── app.py                  # Main Flask application (routes, APIs)
├── database.py             # Database operations (CRUD, queries)
├── scraper.py               # Web scraping engine (7+ stores)
├── index.html               # Home page (hero, stats, categories)
├── search.html               # Search page (filters, results)
├── compare.html               # Compare page (cross-store comparison)
├── admin.html               # Admin dashboard (scrape control, logs)
├── product.html               # Product detail page (history, stats)
├── static/
│   └── images/
│       └── placeholder.svg  # Placeholder image for missing images
├── requirements.txt        # Python dependencies
└── README.md                # This file
```

</details>

<br>

## 🔧 Configuration

<details>
<summary><b>Environment variables</b></summary>
<br>

| Variable | Description | Default |
|---|---|---|
| `HATBHAU_HOST` | Host to bind the server to | `0.0.0.0` |
| `HATBHAU_PORT` | Port to run the server on | `5000` |
| `HATBHAU_DEBUG` | Enable/disable debug mode | `1` (True) |
| `HATBHAU_ADMIN_USER` | Admin username for web login | `momo` |
| `HATBHAU_ADMIN_PASS` | Admin password for web login | `momo` |
| `HATBHAU_SECRET_KEY` | Flask session secret key | Auto-generated |
| `HATBHAU_ADMIN_TOKEN` | API bearer token for programmatic access | `hatbhau-dev-token-change-me` |
| `HATBHAU_SCHEDULER_ENABLED` | Enable periodic auto-scraping | `0` (False) |
| `HATBHAU_SCHEDULER_INTERVAL_HOURS` | Auto-scrape interval in hours | `6` |

</details>

<details>
<summary><b>requirements.txt</b></summary>

```txt
Flask==2.3.2
Flask-CORS==4.0.0
requests==2.31.0
beautifulsoup4==4.12.2
selenium==4.11.2
webdriver-manager==3.9.1
lxml==4.9.3
python-dotenv==1.0.0
APScheduler==3.10.4
```

</details>

<br>

## 🎯 Supported Stores

| Store | Country | Currency | Method | Engine |
|---|:---:|:---:|---|:---:|
| Brother Mart | 🇳🇵 Nepal | NPR | Shopify JSON + DOM fallback | `shopify_json` |
| Sinja | 🇳🇵 Nepal | NPR | Shopify JSON + DOM fallback | `shopify_json` |
| 91mobiles | 🇮🇳 India | INR | Brand-by-brand crawl *(ref. only)* | `brand_crawl` |
| PriceOye | 🇵🇰 Pakistan | PKR | Selenium JS extraction | `selenium_js` |
| Fatafat Sewa | 🇳🇵 Nepal | NPR | BFS site crawl | `bfs_crawl` |
| GadgetByte Nepal | 🇳🇵 Nepal | NPR | Table/heading parser *(ref. only)* | `table_heading` |
| Daraz | 🇳🇵 Nepal | NPR | HTML search + Selenium fallback | `html_search` |

> Currency is isolated per store — cross-currency comparisons are never mixed.

<br>

## 📊 API Reference

<details>
<summary><b>🌐 Public endpoints</b> <sub>(14)</sub></summary>
<br>

| Endpoint | Method | Description | Parameters |
|---|:---:|---|---|
| `/` | `GET` | Home page | — |
| `/search` | `GET` | Search page | `q`, `category`, `sort_by` |
| `/compare` | `GET` | Compare page | — |
| `/product/<id>` | `GET` | Product detail page | `id` |
| `/admin` | `GET` | Admin page | — |
| `/api/sources` | `GET` | List all stores | — |
| `/api/products` | `GET` | Paginated products with filters | `page`, `per_page`, `q`, `store`, `category`, `min_price`, `max_price`, `min_rating`, `min_discount`, `sort_by` |
| `/api/search` | `GET` | Search products | same as `/api/products` |
| `/api/products/<id>` | `GET` | Get product details | `id` |
| `/api/history/<id>` | `GET` | Get price history | `id` |
| `/api/categories` | `GET` | List categories with counts | — |
| `/api/compare` | `GET` | Compare products across stores | `q` |
| `/api/stats` | `GET` | Dashboard statistics | — |
| `/api/health` | `GET` | Health check | — |
| `/api/export/csv` | `GET` | Export all products as CSV | — |

</details>

<details>
<summary><b>🔒 Admin endpoints</b> <sub>(requires bearer token — 7)</sub></summary>
<br>

| Endpoint | Method | Description | Parameters |
|---|:---:|---|---|
| `/admin/login` | `POST` | Admin login | `username`, `password` |
| `/admin/logout` | `GET` | Admin logout | — |
| `/api/scrape` | `POST` | Trigger scraping | `{"source": "all"}` or `{"sources": [...]}` |
| `/api/scrape/status` | `GET` | Real-time scrape status | — |
| `/api/scrape/results` | `GET` | Per-source scrape results | — |
| `/api/scrape/custom` | `POST` | Scrape a custom URL | `{"url", "max_pages", "render_js"}` |
| `/api/admin/health` | `GET` | Store health status | — |
| `/api/admin/logs` | `GET` | Scrape logs | `limit` (default 30) |
| `/api/admin/clear/<source>` | `POST` | Clear store data | `source` |

</details>

<details>
<summary><b>💻 Example requests</b></summary>
<br>

```bash
# Search for products
curl "http://localhost:5000/api/search?q=iphone&sort_by=discount"

# Get product details
curl "http://localhost:5000/api/products/123"

# Trigger scraping — all sources
curl -X POST http://localhost:5000/api/scrape \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer hatbhau-dev-token-change-me" \
  -d '{"source": "all"}'

# Trigger scraping — specific stores
curl -X POST http://localhost:5000/api/scrape \
  -H "Content-Type: application/json" \
  -d '{"sources": ["daraz", "brother_mart"]}'

# Export data
curl -O http://localhost:5000/api/export/csv
```

</details>

<br>

## 🧠 How It Works

<details>
<summary><b>Scraping architecture — 5 stages</b></summary>
<br>

1. **Engine Dispatch** — each store uses a specific engine configured in `scraper.py`
2. **Concurrent Scraping** — non-Selenium stores scrape in parallel via `ThreadPoolExecutor`
3. **Data Normalization** — all products normalized to one consistent schema
4. **Price History** — previous prices retained for trend analysis
5. **Fuzzy Matching** — products matched across stores via brand detection + token similarity

</details>

<br>

## 🗄 Database Schema

<details>
<summary><b><code>products</code></b></summary>
<br>

| Field | Type |
|---|---|
| `id` | `INTEGER PRIMARY KEY` |
| `source` | `TEXT` |
| `name` | `TEXT` |
| `price` | `REAL` |
| `currency` | `TEXT` |
| `original_price` | `REAL` |
| `discount_percent` | `REAL` |
| `url`, `image_url` | `TEXT` |
| `category` | `TEXT` |
| `rating`, `reviews` | `REAL`, `INTEGER` |
| `availability` | `TEXT` |
| `scraped_at` | `TEXT` |

</details>

<details>
<summary><b><code>price_history</code></b></summary>
<br>

| Field | Type |
|---|---|
| `id` | `INTEGER PRIMARY KEY` |
| `source`, `url` | `TEXT` |
| `price` | `REAL` |
| `currency` | `TEXT` |
| `recorded_at` | `TEXT` |

</details>

<details>
<summary><b><code>scrape_log</code></b></summary>
<br>

| Field | Type |
|---|---|
| `id` | `INTEGER PRIMARY KEY` |
| `source` | `TEXT` |
| `status` | `success` / `failed` / `partial` |
| `products_found` | `INTEGER` |
| `message` | `TEXT` |
| `duration_seconds` | `REAL` |
| `started_at`, `finished_at` | `TEXT` |

</details>

<details>
<summary><b><code>phone_groups</code> / <code>group_members</code></b> <sub>(fuzzy matching)</sub></summary>
<br>

| Field | Type |
|---|---|
| `group_id` | `TEXT PRIMARY KEY` |
| `base_name` | `TEXT` |
| `created_at` | `TEXT` |
| `group_members.product_id` | `INTEGER` |
| `group_members.source` | `TEXT` |

</details>

<br>

## 🔒 Security

- Session-based authentication for the admin UI (Flask sessions)
- Bearer token authentication for API access
- Configurable secret key for session security
- Input sanitization on all user inputs
- CORS configured with credentials support
- SQL injection protection via parameterized queries

<br>

## 🐛 Troubleshooting

<details>
<summary>Selenium WebDriver not found</summary>
<br>

```bash
pip install webdriver-manager
# the first run automatically downloads the correct ChromeDriver
```

</details>

<details>
<summary>Database locked</summary>
<br>

```bash
# the app uses WAL mode, but if you hit locks:
rm hatbhau.db-shm hatbhau.db-wal
# Windows:
del hatbhau.db-shm hatbhau.db-wal
```

</details>

<details>
<summary>Port 5000 already in use</summary>
<br>

```bash
export HATBHAU_PORT=5001
python app.py
```

</details>

<details>
<summary>Scraping fails for a store</summary>
<br>

- Check whether the store's website structure changed
- Update selectors in the `SITES` config in `scraper.py`
- Check network connectivity and rate limiting
- Verify the store is reachable from your location

</details>

<details>
<summary>No products found after scraping</summary>
<br>

- Check the scrape logs in the admin panel
- Verify the store's website is accessible
- Try the custom URL scraper for a quick test

</details>

<details>
<summary>PriceOye scraping issues</summary>
<br>

- Ensure Chrome is installed
- Check that Selenium can launch Chrome headlessly
- The JS extractor may need updating if the site changes

</details>

<br>

## 🤝 Contributing

Contributions are welcome!

```bash
git checkout -b feature/amazing-feature
git commit -m 'Add amazing feature'
git push origin feature/amazing-feature
# then open a Pull Request
```

<details>
<summary><b>Adding a new store</b></summary>
<br>

1. Add configuration to the `SITES` dictionary in `scraper.py`
2. Specify the scraping engine (or create a new one)
3. Add selectors and parsing logic
4. Test thoroughly
5. Update this README with the new store's information

</details>

<details>
<summary><b>Code style</b></summary>
<br>

- Follow PEP 8 guidelines
- Use meaningful variable names
- Add docstrings for functions and classes
- Comment complex logic

</details>

<br>

## 🚦 Roadmap

<details open>
<summary><b>Short Term</b></summary>

- [ ] Add more Nepali e-commerce stores (Sastodeal, etc.)
- [ ] Improve price history visualization (charts)
- [ ] Add price drop notifications
- [ ] Enhance fuzzy matching algorithm

</details>

<details>
<summary><b>Medium Term</b></summary>

- [ ] User accounts and wishlists
- [ ] Email alerts for price drops
- [ ] Mobile app (React Native)
- [ ] Multi-language support (Nepali, English)
- [ ] Telegram / Discord bots for price alerts

</details>

<details>
<summary><b>Long Term</b></summary>

- [ ] Price prediction using ML
- [ ] Store rating system
- [ ] Social sharing features
- [ ] Browser extension
- [ ] API rate limiting and caching

</details>

<br>

## 🛡️ Disclaimer

This project is for educational purposes. Please respect the terms of service of the websites being scraped. The developers are not responsible for any misuse of this software. Use responsibly and consider each website's `robots.txt` and rate limits.

<br>

## 📝 License

Licensed under the [MIT License](LICENSE).

<br>

<div align="center">

**Made with ❤️ in Nepal**

*हट भाउ — Find the best deal, every time.*

</div>
