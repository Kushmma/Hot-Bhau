"""
HatBhau (हट भाउ) — Admin Flask Application
Runs on port 5001 for admin users
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
HOST = os.getenv("HATBHAU_HOST", "0.0.0.0")
PORT = int(os.getenv("HATBHAU_ADMIN_PORT", "5001"))
DEBUG = os.getenv("HATBHAU_DEBUG", "1") == "1"

ADMIN_TOKEN = os.getenv("HATBHAU_ADMIN_TOKEN", "hatbhau-dev-token-change-me")
ADMIN_USERNAME = os.getenv("HATBHAU_ADMIN_USER", "momo")
ADMIN_PASSWORD = os.getenv("HATBHAU_ADMIN_PASS", "momo")
SECRET_KEY = os.getenv("HATBHAU_SECRET_KEY", "hatbhau-dev-secret-change-me")

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
            return redirect(url_for("admin_login_page"))
        return f(*args, **kwargs)
    return wrapped


# ── SESSION CHECK MIDDLEWARE ──────────────────────────────────────────
@app.before_request
def check_admin_session():
    if request.path.startswith('/static') or request.path == '/admin/login' or request.path.startswith('/admin/login'):
        return None
    if request.path.startswith('/admin') and not session.get('admin'):
        return None
    return None


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


# ── PAGE ROUTES ──────────────────────────────────────────────────────
@app.route("/admin/login")
def admin_login_page():
    if session.get("admin"):
        return redirect(url_for("admin_page"))
    return render_template("admin_login.html")


@app.route("/admin/login", methods=["POST"])
def admin_login():
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        session["admin"] = True
        session.permanent = True
        return redirect(url_for("admin_page"))
    return render_template("admin_login.html", error="Invalid username or password")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("admin_login_page"))


@app.route("/admin")
@require_admin
def admin_page():
    return render_template("admin.html")


# ── API ROUTES ──────────────────────────────────────────────────────

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


@app.route("/api/admin/db/stats")
@require_admin
def api_admin_db_stats():
    try:
        all_products = database.get_all_products()
        return jsonify({
            "total_products": len(all_products),
            "total_stores": len(database.get_distinct_sources()),
            "db_size": "N/A"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/clear/all", methods=["POST"])
@require_admin
def api_admin_clear_all():
    for source in SITES:
        database.clear_source(source)
    return jsonify({"status": "cleared", "message": "All data cleared"})


@app.route("/api/scrape", methods=["POST"])
@require_admin
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
@require_admin
def api_scrape_status():
    return jsonify(scrape_status)


@app.route("/api/scrape/results")
@require_admin
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
@require_admin
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
        path = os.path.join(tempfile.gettempdir(), "hatbhau_export.csv")
        if products:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=list(products[0].keys()))
                writer.writeheader()
                writer.writerows(products)
        else:
            open(path, "w").close()
        return send_file(path, as_attachment=True, download_name="hatbhau_products.csv")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
    return render_template("admin_login.html"), 404


@app.errorhandler(500)
def server_error(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Internal server error"}), 500
    return render_template("admin_login.html"), 500


# ── MAIN ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    database.init_db()
    print("=" * 58)
    print("  HatBhau (हट भाउ) — Admin Interface")
    print(f"  Admin Login  : http://localhost:{PORT}/admin/login  (momo/momo)")
    print(f"  Admin Panel  : http://localhost:{PORT}/admin")
    print(f"  Scrape       : POST http://localhost:{PORT}/api/scrape")
    print(f"  Status       : http://localhost:{PORT}/api/scrape/status")
    print(f"  Export CSV   : http://localhost:{PORT}/api/export/csv")
    print("=" * 58)
    app.run(host=HOST, port=PORT, debug=DEBUG, threaded=True, use_reloader=False)