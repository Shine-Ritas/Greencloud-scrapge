import atexit
import hmac
import secrets
import threading
from flask import Flask, Response, jsonify, render_template, request
from .config import Settings
from .service import Monitor
from .telegram import TelegramError, send_message


def create_app(settings=None, start_scheduler=True, monitor=None):
    settings = settings or Settings.from_env()
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 4096
    token = secrets.token_urlsafe(32)
    monitor = monitor or Monitor(settings)
    app.extensions["monitor"] = monitor

    @app.before_request
    def protect():
        if request.path == "/healthz":
            return None
        if settings.dashboard_password:
            auth = request.authorization
            if (not auth or not hmac.compare_digest((auth.username or "").encode(), settings.dashboard_user.encode())
                    or not hmac.compare_digest((auth.password or "").encode(), settings.dashboard_password.encode())):
                return Response("Authentication required", 401, {"WWW-Authenticate": 'Basic realm="GreenWatch", charset="UTF-8"'})
        if request.method == "POST" and not hmac.compare_digest(request.headers.get("X-CSRF-Token", "").encode(), token.encode()):
            return jsonify(error="Invalid action token. Refresh the dashboard."), 403

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        return response

    @app.get("/")
    def index():
        return render_template("index.html", csrf_token=token)

    @app.get("/healthz")
    def health():
        try:
            with monitor.db.connect() as db:
                db.execute("SELECT 1").fetchone()
            alive = not start_scheduler or bool(monitor.scheduler and monitor.scheduler.is_alive())
            return jsonify(status="ok" if alive else "error"), 200 if alive else 503
        except Exception:
            return jsonify(status="error"), 503

    @app.get("/api/status")
    def status():
        return jsonify(monitor.snapshot())

    @app.post("/api/scrape")
    def scrape():
        if not monitor.request_scrape("manual"):
            return jsonify(error="A scrape or alert delivery is already running"), 409
        return jsonify(message="Scrape started"), 202

    test_lock = threading.Lock()
    last_test = [float("-inf")]

    @app.post("/api/telegram/test")
    def test_telegram():
        if not settings.telegram_configured:
            return jsonify(error="Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env, then recreate the container"), 400
        if not test_lock.acquire(blocking=False):
            return jsonify(error="A test notification is already running"), 409
        try:
            now = monitor.clock()
            if now - last_test[0] < 60:
                return jsonify(error="Wait 60 seconds between test notifications"), 429
            last_test[0] = now
            try:
                send_message(settings, "GreenWatch test: Telegram channel notifications are connected. Watching APAC KVMs at $25 USD/year with at least 1 unit available.")
            except TelegramError as error:
                return jsonify(error=str(error)), 502
            return jsonify(message="Test notification delivered")
        finally:
            test_lock.release()

    @app.get("/api/products/<product_id>/history")
    def history(product_id):
        with monitor.db.connect() as db:
            rows = db.execute("SELECT observed_at,stock,price_cents,eligible FROM observations WHERE product_id=? ORDER BY observed_at DESC LIMIT 120", (product_id,)).fetchall()
        return jsonify([dict(r) for r in rows][::-1])

    if start_scheduler:
        monitor.start()
        atexit.register(monitor.stop)
    return app
