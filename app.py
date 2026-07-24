# app.py - Updated with admin blueprint and real-time updates

import os
import sys
import threading
import time
import tempfile
import traceback
from datetime import datetime
from functools import wraps
from flask import (
    Flask, jsonify, request, send_file, abort, render_template,
    session, redirect, url_for, Blueprint, g
)
from flask_cors import CORS
import database
import scraper
from scraper import SITES
import json

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── APP CONFIG ───────────────────────────────────────────────────────
HOST = os.getenv("BESTBHAU_HOST", "0.0.0.0")
PORT = int(os.getenv("BESTBHAU_PORT", "5000"))
DEBUG = os.getenv("BESTBHAU_DEBUG", "1") == "1"

ADMIN_USERNAME = os.getenv("BESTBHAU_ADMIN_USER", "momo")
ADMIN_PASSWORD = os.getenv("BESTBHAU_ADMIN_PASS", "momo")
SECRET_KEY = os.getenv("BESTBHAU_SECRET_KEY", "BESTBHAU-dev-secret-change-me")

app = Flask(__name__, 
            template_folder="templates",
            static_folder="static",
            static_url_path="/static")
app.secret_key = SECRET_KEY
CORS(app, supports_credentials=True)

# ── ADMIN BLUEPRINT ──────────────────────────────────────────────
admin_bp = Blueprint('admin', __name__, url_prefix='/admin', 
                     template_folder='templates/admin',
                     static_folder='static/admin')

# ── AUTH ────────────────────────────────────────────────────────────────
def is_admin_request() -> bool:
    if session.get("admin"):
        return True
    auth = request.headers.get("Authorization", "")
    return auth.startswith("Bearer ") and auth[7:] == os.getenv("BESTBHAU_ADMIN_TOKEN", "BESTBHAU-dev-token-change-me")

def require_admin(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not is_admin_request():
            if request.path.startswith("/admin/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("admin.admin_page"))
        return f(*args, **kwargs)
    return wrapped

# ── SCRAPE STATUS (shared) ─────────────────────────────────────────────
scrape_status = {
    "running": False,
    "message": "",
    "started_at": None,
    "progress": 0,
    "current_source": None,
    "total_sources": 0,
    "sources_done": 0,
    "results": {},
    "last_update": None,
}

# ── USER ROUTES ──────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/search")
def search_page():
    return render_template("search.html")

@app.route("/compare")
def compare_page():
    return render_template("compare.html")

@app.route("/product/<int:product_id>")
def product_page(product_id):
    return render_template("product.html", product_id=product_id)

# ── ADMIN ROUTES ──────────────────────────────────────────────────────
@admin_bp.route("/")
def admin_page():
    return render_template("admin.html")

@admin_bp.route("/login", methods=["POST"])
def admin_login():
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        session["admin"] = True
    return redirect(url_for("admin.admin_page"))

@admin_bp.route("/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("admin.admin_page"))

# ── ADMIN API ROUTES ──────────────────────────────────────────────────
@admin_bp.route("/api/health")
@require_admin
def api_admin_health():
    rows = []
    for key, cfg in SITES.items():
        stats = database.get_stats_by_source(key) or {}
        rows.append({
            "source": key,
            "label": cfg["label"],
            "status": stats.get("status", "idle"),
            "product_count": stats.get("total", 0),
            "last_successful_scrape": stats.get("last_scrape"),
            "last_error": stats.get("message") if stats.get("status") == "failed" else None,
        })
    return jsonify({"health": rows})

@admin_bp.route("/api/logs")
@require_admin
def api_admin_logs():
    limit = int(request.args.get("limit", 30))
    return jsonify({"logs": database.get_recent_scrape_logs(limit)})

@admin_bp.route("/api/clear/<source>", methods=["POST"])
@require_admin
def api_admin_clear(source):
    if source not in SITES and source != "all":
        return jsonify({"error": f"Unknown source: {source}"}), 400
    if source == "all":
        for key in SITES:
            database.clear_source(key)
        return jsonify({"status": "cleared", "source": "all"})
    database.clear_source(source)
    return jsonify({"status": "cleared", "source": source})

@admin_bp.route("/api/db/stats")
@require_admin
def api_admin_db_stats():
    return jsonify(database.get_db_stats())

@admin_bp.route("/api/scrape", methods=["POST"])
@require_admin
def api_admin_scrape():
    if scrape_status["running"]:
        return jsonify({"error": "Scraper already running"}), 409
    
    data = request.get_json(silent=True) or {}
    sources = data.get("sources")
    source = data.get("source", "all")
    
    if sources:
        selected_keys = [s for s in sources if s in SITES]
    elif source == "all":
        selected_keys = list(SITES.keys())
    else:
        selected_keys = [source] if source in SITES else []
    
    if not selected_keys:
        return jsonify({"error": "No valid sources specified"}), 400
    
    def run_job():
        global scrape_status
        from scraper import run_selected
        
        scrape_status.update({
            "running": True,
            "started_at": datetime.now().isoformat(),
            "progress": 0,
            "message": "Starting scrape...",
            "current_source": None,
            "total_sources": len(selected_keys),
            "sources_done": 0,
            "results": {},
            "last_update": datetime.now().isoformat(),
        })
        
        try:
            results = run_selected(selected_keys)
            total = sum(r.get("count", 0) for r in results.values())
            scrape_status.update({
                "running": False,
                "progress": 100,
                "message": f"✅ Scraped {total} products from {len(results)} stores",
                "results": results,
                "last_update": datetime.now().isoformat(),
            })
        except Exception as e:
            scrape_status.update({
                "running": False,
                "progress": 100,
                "message": f"❌ Error: {str(e)[:150]}",
                "last_update": datetime.now().isoformat(),
            })
    
    threading.Thread(target=run_job, daemon=True).start()
    return jsonify({"status": "started", "message": f"Scraping {len(selected_keys)} stores"})

@admin_bp.route("/api/scrape/status")
@require_admin
def api_admin_scrape_status():
    return jsonify(scrape_status)

@admin_bp.route("/api/scrape/custom", methods=["POST"])
@require_admin
def api_admin_scrape_custom():
    if scrape_status["running"]:
        return jsonify({"error": "Scraper already running"}), 409
    
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Provide a 'url' to scrape"}), 400
    
    max_pages = min(int(data.get("max_pages", 20)), 200)
    render_js = bool(data.get("render_js", False))
    
    def run_job():
        global scrape_status
        try:
            from scraper import scrape_any_url
            scrape_status.update({
                "running": True,
                "started_at": datetime.now().isoformat(),
                "progress": 0,
                "message": f"Scraping {url}...",
                "current_source": "custom",
                "last_update": datetime.now().isoformat(),
            })
            
            result = scrape_any_url(url, max_pages=max_pages, render_js=render_js)
            scrape_status.update({
                "running": False,
                "progress": 100,
                "message": f"✅ Done! Found {result.get('count', 0)} products",
                "last_update": datetime.now().isoformat(),
            })
        except Exception as e:
            scrape_status.update({
                "running": False,
                "progress": 100,
                "message": f"❌ Error: {str(e)[:150]}",
                "last_update": datetime.now().isoformat(),
            })
    
    threading.Thread(target=run_job, daemon=True).start()
    return jsonify({"status": "started", "message": f"Scraping {url}"})

# ── USER API ROUTES ──────────────────────────────────────────────────
@app.route("/api/sources")
def api_sources():
    return jsonify({
        key: {"label": cfg["label"], "country": cfg["country"], "currency": cfg["currency"]}
        for key, cfg in SITES.items()
    })

@app.route("/api/products")
def api_products():
    try:
        params = _query_params_from_request()
        result = database.query_products(**params)
        return jsonify(_paginated_response(result))
    except Exception as e:
        print(f"[API Error] /api/products: {e}")
        traceback.print_exc()
        return jsonify({"error": f"Database error: {str(e)}"}), 500

@app.route("/api/search")
def api_search():
    return api_products()

@app.route("/api/products/<int:product_id>")
def api_product_detail(product_id):
    p = database.get_product(product_id)
    if not p:
        return jsonify({"error": "not found"}), 404
    return jsonify(_serialize(p))

@app.route("/api/history/<int:product_id>")
def api_history(product_id):
    history = database.get_price_history_by_id(product_id)
    if not history:
        return jsonify({"history": [], "stats": {}})
    prices = [h["price"] for h in history]
    stats = {
        "lowest": min(prices), "highest": max(prices),
        "average": round(sum(prices) / len(prices), 2), "current": prices[-1],
    }
    return jsonify({"history": history, "stats": stats})

@app.route("/api/categories")
def api_categories():
    return jsonify(database.get_categories())

@app.route("/api/compare")
def api_compare():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"all": [], "cheapest": None, "by_store": {}, "total": 0})
    raw = database.compare_query(q)
    all_items = [_serialize(p) for p in raw["all"]]
    by_store = {}
    for p in all_items:
        by_store.setdefault(p["store"], []).append(p)
    return jsonify({
        "all": all_items,
        "cheapest": all_items[0] if all_items else None,
        "by_store": by_store,
        "total": raw["total"],
    })

@app.route("/api/stats")
def api_stats():
    try:
        all_products = database.get_all_products()
        prices = [p["price"] for p in all_products]
        discounts = [p["discount_percent"] for p in all_products if p.get("discount_percent")]
        categories = database.get_categories()
        
        active_stores = 0
        last_scrape = None
        for key in SITES:
            stats = database.get_stats_by_source(key)
            if stats and stats.get("total", 0) > 0:
                active_stores += 1
                if stats.get("last_scrape"):
                    if not last_scrape or stats["last_scrape"] > last_scrape:
                        last_scrape = stats["last_scrape"]

        # Get scrape progress for user display
        scrape_progress = None
        if scrape_status["running"]:
            scrape_progress = {
                "running": True,
                "progress": scrape_status["progress"],
                "message": scrape_status["message"],
                "current_source": scrape_status["current_source"],
                "total_sources": scrape_status["total_sources"],
                "sources_done": scrape_status["sources_done"],
            }
        elif scrape_status.get("last_update"):
            scrape_progress = {
                "running": False,
                "message": scrape_status.get("message", ""),
                "last_update": scrape_status.get("last_update"),
            }

        return jsonify({
            "total_products": len(all_products),
            "total_stores": active_stores,
            "total_categories": len(categories),
            "avg_discount": round(sum(discounts) / len(discounts), 1) if discounts else 0,
            "min_price": min(prices) if prices else None,
            "max_price": max(prices) if prices else None,
            "avg_price": round(sum(prices) / len(prices), 2) if prices else None,
            "last_scrape": last_scrape,
            "scrape_progress": scrape_progress,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/export/csv")
def export_csv():
    try:
        import csv
        products = database.get_all_products()
        path = os.path.join(tempfile.gettempdir(), "BESTBHAU_export.csv")
        if products:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=list(products[0].keys()))
                writer.writeheader()
                writer.writerows(products)
        else:
            open(path, "w").close()
        return send_file(path, as_attachment=True, download_name="BESTBHAU_products.csv")
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/health")
def health_check():
    try:
        count = len(database.get_all_products())
        return jsonify({"status": "healthy", "products_in_db": count,
                         "timestamp": datetime.now().isoformat()})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

# ── HELPER FUNCTIONS ──────────────────────────────────────────────────
def _serialize(p: dict) -> dict:
    cfg = SITES.get(p["source"], {})
    return {
        "id": p["id"],
        "name": p["name"],
        "price": p["price"],
        "original_price": p.get("original_price"),
        "discount": p.get("discount_percent") or 0,
        "image_url": p.get("image_url"),
        "product_url": p.get("url"),
        "store": cfg.get("label", p["source"]),
        "source": p["source"],
        "category": p.get("category") or "General",
        "rating": p.get("rating"),
        "reviews": p.get("reviews"),
        "availability": p.get("availability") or "Unknown",
        "last_updated": p.get("scraped_at"),
    }

def _paginated_response(result: dict) -> dict:
    return {**result, "items": [_serialize(p) for p in result["items"]]}

def _query_params_from_request():
    def _f(name):
        v = request.args.get(name)
        try:
            return float(v) if v not in (None, "") else None
        except ValueError:
            return None

    store_val = request.args.get("store") or None
    if store_val:
        if store_val in SITES:
            store = store_val
        else:
            matched = next((k for k, cfg in SITES.items() if cfg.get("label", "").lower() == store_val.lower()), None)
            store = matched or store_val
    else:
        store = None

    return dict(
        q=request.args.get("q") or None,
        store=store,
        category=request.args.get("category") or None,
        min_price=_f("min_price"),
        max_price=_f("max_price"),
        min_rating=_f("min_rating"),
        min_discount=_f("min_discount"),
        sort_by=request.args.get("sort_by", "updated"),
        page=int(request.args.get("page", 1)),
        per_page=int(request.args.get("per_page", 20)),
    )

# ── REGISTER BLUEPRINTS ──────────────────────────────────────────────
app.register_blueprint(admin_bp)

# ── ERROR HANDLERS ──────────────────────────────────────────────────
@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found"}), 404
    return render_template("index.html"), 404

@app.errorhandler(500)
def server_error(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Internal server error"}), 500
    return render_template("index.html"), 500

# ── MAIN ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    database.init_db()
    print("=" * 58)
    print("  BESTBHAU (बेस्ट भाउ) — Nepali Price Comparison Engine")
    print(f"  Sources: {', '.join(cfg['label'] for cfg in SITES.values())}")
    print("=" * 58)
    print(f"  User Site    : http://localhost:{PORT}")
    print(f"  Admin Panel  : http://localhost:{PORT}/admin  (momo/momo)")
    print(f"  Search       : http://localhost:{PORT}/search?q=iphone")
    print(f"  Compare      : http://localhost:{PORT}/compare?q=iphone")
    print("=" * 58)
    app.run(host=HOST, port=PORT, debug=DEBUG, threaded=True, use_reloader=False)