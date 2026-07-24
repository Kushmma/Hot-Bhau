"""
Best Bhau (बेस्ट भाउ) — Flask Application
(no changes except better error handling in API endpoints)
"""

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
    session, redirect, url_for,
)
from flask_cors import CORS

import database
import scraper
from scraper import SITES

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── APP CONFIG ───────────────────────────────────────────────────────
HOST = os.getenv("BESTBHAU_HOST", "0.0.0.0")
PORT = int(os.getenv("BESTBHAU_PORT", "5000"))
DEBUG = os.getenv("BESTBHAU_DEBUG", "1") == "1"

ADMIN_TOKEN = os.getenv("BESTBHAU_ADMIN_TOKEN", "BESTBHAU-dev-token-change-me")
ADMIN_USERNAME = os.getenv("BESTBHAU_ADMIN_USER", "momo")
ADMIN_PASSWORD = os.getenv("BESTBHAU_ADMIN_PASS", "momo")
SECRET_KEY = os.getenv("BESTBHAU_SECRET_KEY", "BESTBHAU-dev-secret-change-me")

SCHEDULER_ENABLED = os.getenv("BESTBHAU_SCHEDULER_ENABLED", "0") == "1"
SCHEDULER_INTERVAL_HOURS = int(os.getenv("BESTBHAU_SCHEDULER_INTERVAL_HOURS", "6"))

app = Flask(__name__, template_folder=".", static_folder="static")
app.secret_key = SECRET_KEY
CORS(app, supports_credentials=True)

scrape_status = {
    "running": False,
    "message": "",
    "started_at": None,
    "progress": 0,
    "current_source": None,
    "total_sources": 0,
    "sources_done": 0,
    "results": {},
}


# ── AUTH ────────────────────────────────────────────────────────────────
def is_admin_request() -> bool:
    if session.get("admin"):
        return True
    auth = request.headers.get("Authorization", "")
    return auth.startswith("Bearer ") and auth[7:] == ADMIN_TOKEN


def require_admin(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not is_admin_request():
            if request.path.startswith("/api/"):
                abort(401, description="Missing or invalid admin session/bearer token")
            return redirect(url_for("admin_page"))
        return f(*args, **kwargs)
    return wrapped


# ── PRODUCT SERIALIZATION ─────────────────────────────────────────────
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


# ── PAGE ROUTES ──────────────────────────────────────────────────────
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


@app.route("/admin")
def admin_page():
    return render_template("admin.html")


@app.route("/admin/login", methods=["POST"])
def admin_login():
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        session["admin"] = True
    return redirect(url_for("admin_page"))


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("admin_page"))


# ── API ROUTES ──────────────────────────────────────────────────────

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
        # Log the full traceback for debugging
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


@app.route("/api/comparisons")
def api_comparisons():
    try:
        comps = database.get_comparisons()
        return jsonify({"comparisons": comps, "count": len(comps),
                         "timestamp": datetime.now().isoformat()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/price-history")
def api_price_history():
    source = request.args.get("source")
    url = request.args.get("url")
    if not source or not url:
        return jsonify({"error": "source and url query params are required"}), 400
    try:
        history = database.get_price_history(source, url)
        return jsonify({"source": source, "url": url, "history": history})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/stats")
def api_stats():
    try:
        all_products = database.get_all_products()
        prices = [p["price"] for p in all_products]
        discounts = [p["discount_percent"] for p in all_products if p.get("discount_percent")]
        categories = database.get_categories()
        per_source = {key: database.get_stats_by_source(key) for key in SITES}
        active_stores = len([s for s in per_source.values() if s and s["total"]])

        last_scrape = None
        for s in per_source.values():
            if s and s.get("last_scrape"):
                if not last_scrape or s["last_scrape"] > last_scrape:
                    last_scrape = s["last_scrape"]

        return jsonify({
            "total_products": len(all_products),
            "total_stores": active_stores,
            "total_categories": len(categories),
            "avg_discount": round(sum(discounts) / len(discounts), 1) if discounts else 0,
            "total": len(all_products),
            "min_price": min(prices) if prices else None,
            "max_price": max(prices) if prices else None,
            "avg_price": round(sum(prices) / len(prices), 2) if prices else None,
            "active_sources": active_stores,
            "last_scrape": last_scrape,
            "sources": per_source,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── SCRAPE ENDPOINTS ─────────────────────────────────────────────────

@app.route("/api/scrape", methods=["POST"])
def api_trigger_scrape():
    if scrape_status["running"]:
        return jsonify({"error": "Scraper already running. Wait for it to finish."}), 409

    data = request.get_json(silent=True) or {}
    sources_list = data.get("sources")
    target = data.get("source", "all" if not sources_list else None)

    if sources_list is not None:
        if not isinstance(sources_list, list) or not sources_list:
            return jsonify({"error": "'sources' must be a non-empty list"}), 400
        unknown = [s for s in sources_list if s not in SITES]
        if unknown:
            return jsonify({"error": f"Unknown source(s): {', '.join(unknown)}"}), 400
        selected_keys = sources_list
    elif target == "all":
        selected_keys = list(SITES.keys())
    else:
        if target not in SITES:
            return jsonify({"error": f"Unknown source: {target}"}), 400
        selected_keys = [target]

    total_targets = len(selected_keys)

    def run_job():
        scrape_status.update(
            running=True,
            started_at=datetime.now().isoformat(),
            progress=0,
            message="Starting...",
            current_source=None,
            total_sources=total_targets,
            sources_done=0,
            results={}
        )
        done_count = {"n": 0}
        results = {}

        orig_run_scrape = scraper.run_scrape

        def tracked_run_scrape(key, force=False):
            scrape_status["current_source"] = key
            label = SITES[key]["label"]
            scrape_status["message"] = f"Scraping {label}..."
            t0 = time.time()
            result = orig_run_scrape(key, force=force)
            duration = time.time() - t0
            done_count["n"] += 1
            scrape_status["sources_done"] = done_count["n"]
            scrape_status["progress"] = round(done_count["n"] / total_targets * 100)
            results[key] = {
                "label": label,
                "status": result.get("status", "success"),
                "count": result.get("count", 0),
                "duration": round(duration, 2),
                "finished_at": datetime.now().isoformat(),
            }
            scrape_status["results"] = results
            return result

        scraper.run_scrape = tracked_run_scrape
        try:
            scraper.run_selected(selected_keys)
            total_found = sum(r.get("count", 0) for r in results.values())
            n = len(results)
        except Exception as e:
            scrape_status.update(
                running=False,
                progress=100,
                current_source=None,
                message=f"Error: {str(e)[:150]}"
            )
            return
        finally:
            scraper.run_scrape = orig_run_scrape

        scrape_status.update(
            running=False,
            progress=100,
            current_source=None,
            message=f"Done! Scraped {total_found} products from {n} source(s).",
        )

    threading.Thread(target=run_job, daemon=True).start()
    label_str = "all sources" if total_targets == len(SITES) else f"{total_targets} selected source(s)"
    return jsonify({"status": "started",
                     "message": f"Scraping {label_str} in background. Poll /api/scrape/status."})


@app.route("/api/scrape/status")
def api_scrape_status():
    return jsonify(scrape_status)


@app.route("/api/scrape/results")
def api_scrape_results():
    try:
        results = {}
        for key in SITES:
            stats = database.get_stats_by_source(key)
            if stats:
                results[key] = {
                    "label": SITES[key]["label"],
                    "status": stats.get("status", "idle"),
                    "count": stats.get("total", 0),
                    "last_scrape": stats.get("last_scrape"),
                    "message": stats.get("message", ""),
                }
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scrape/custom", methods=["POST"])
def api_scrape_custom():
    if scrape_status["running"]:
        return jsonify({"error": "Scraper already running. Wait for it to finish."}), 409

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Provide a 'url' to scrape"}), 400
    max_pages = min(int(data.get("max_pages", 20)), 200)
    render_js = bool(data.get("render_js", False))

    def run_job():
        scrape_status.update(
            running=True,
            started_at=datetime.now().isoformat(),
            progress=0,
            message=f"Crawling {url} ...",
            current_source="custom",
            results={}
        )
        try:
            result = scraper.scrape_any_url(url, max_pages=max_pages, render_js=render_js)
            total = result.get("count", 0)
        except Exception as e:
            scrape_status.update(
                running=False,
                progress=100,
                current_source=None,
                message=f"Error: {str(e)[:150]}"
            )
            return
        scrape_status.update(
            running=False,
            progress=100,
            current_source=None,
            message=f"Done! Found {total} product(s) at {url}."
        )

    threading.Thread(target=run_job, daemon=True).start()
    return jsonify({"status": "started",
                     "message": f"Crawling '{url}' in background (up to {max_pages} pages). "
                                f"Poll /api/scrape/status."})


# ── EXPORT ───────────────────────────────────────────────────────────

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


# ── ADMIN ────────────────────────────────────────────────────────────

@app.route("/api/admin/health")
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


@app.route("/api/admin/logs")
@require_admin
def api_admin_logs():
    limit = int(request.args.get("limit", 30))
    return jsonify({"logs": database.get_recent_scrape_logs(limit)})


@app.route("/api/admin/clear/<source>", methods=["POST"])
@require_admin
def api_admin_clear(source):
    if source not in SITES:
        return jsonify({"error": f"Unknown source: {source}"}), 400
    database.clear_source(source)
    return jsonify({"status": "cleared", "source": source})


# ── HEALTH ───────────────────────────────────────────────────────────

@app.route("/api/health")
def health_check():
    try:
        count = len(database.get_all_products())
        return jsonify({"status": "healthy", "products_in_db": count,
                         "timestamp": datetime.now().isoformat()})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# ── ERROR HANDLERS ──────────────────────────────────────────────────

@app.errorhandler(401)
def unauthorized(e):
    return jsonify({"error": str(e.description)}), 401


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


# ── SCHEDULER ──────────────────────────────────────────────────────

def _maybe_start_scheduler():
    if not SCHEDULER_ENABLED:
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        print("[Scheduler] APScheduler not installed — skipping periodic scrape. "
              "Install with: pip install apscheduler")
        return

    sched = BackgroundScheduler(daemon=True)

    def scheduled_job():
        if not scrape_status["running"]:
            print("[Scheduler] Kicking off scheduled full scrape...")
            try:
                requests_module = __import__("requests")
                requests_module.post(f"http://127.0.0.1:{PORT}/api/scrape",
                                      json={"source": "all"}, timeout=5)
            except Exception as e:
                print(f"[Scheduler] Failed to trigger scrape: {e}")

    sched.add_job(scheduled_job, "interval", hours=SCHEDULER_INTERVAL_HOURS)
    sched.start()
    print(f"[Scheduler] Enabled — running every {SCHEDULER_INTERVAL_HOURS}h")


# ── MAIN ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    database.init_db()
    print("=" * 58)
    print("  BESTBHAU (बेस्ट भाउ) — Nepali Price Comparison Engine")
    print(f"  Sources: {', '.join(cfg['label'] for cfg in SITES.values())}")
    print("=" * 58)
    print(f"  Dashboard    : http://localhost:{PORT}")
    print(f"  Search       : http://localhost:{PORT}/search?q=iphone")
    print(f"  Compare      : http://localhost:{PORT}/compare?q=iphone")
    print(f"  Admin        : http://localhost:{PORT}/admin  (momo/momo)")
    print(f"  Scrape       : POST http://localhost:{PORT}/api/scrape")
    print(f"  Status       : http://localhost:{PORT}/api/scrape/status")
    print(f"  Export CSV   : http://localhost:{PORT}/api/export/csv")
    print("=" * 58)
    _maybe_start_scheduler()
    app.run(host=HOST, port=PORT, debug=DEBUG, threaded=True, use_reloader=False)