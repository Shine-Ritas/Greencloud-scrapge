import json
import logging
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .config import SOURCE_URL
from .db import Database
from .scraper import fetch_page, parse_products, qualifies
from .telegram import send_message

log = logging.getLogger(__name__)


def next_scrape_time(now, timezone):
    local = datetime.fromtimestamp(now, ZoneInfo(timezone))
    for day in range(2):
        date = local.date() + timedelta(days=day)
        for hour in (0, 6, 12, 18):
            candidate = datetime(date.year, date.month, date.day, hour, tzinfo=local.tzinfo).timestamp()
            if candidate > now:
                return candidate


def next_cleanup_time(now, timezone):
    local = datetime.fromtimestamp(now, ZoneInfo(timezone))
    for offset in range(8):
        date = local.date() + timedelta(days=offset)
        candidate = datetime(date.year, date.month, date.day, 3, tzinfo=local.tzinfo)
        if candidate.weekday() == 6 and candidate.timestamp() > now:
            return candidate.timestamp()


class Monitor:
    def __init__(self, settings, fetcher=None, sender=None, clock=None):
        self.settings = settings
        self.db = Database(settings.database)
        self.fetcher = fetcher or fetch_page
        self.sender = sender or send_message
        self.clock = clock or time.time
        self.work_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.scheduler = None
        self.worker = None
        now = self.clock()
        prior_timezone = self.db.get_meta("schedule_timezone")
        if self.db.get_meta("next_scrape_at") is None:
            self.db.set_meta("next_scrape_at", now)
        if self.db.get_meta("next_cleanup_at") is None:
            self.db.set_meta("next_cleanup_at", next_cleanup_time(now, settings.timezone))
        if prior_timezone and prior_timezone != settings.timezone:
            self.db.set_meta("next_scrape_at", next_scrape_time(now, settings.timezone))
            self.db.set_meta("next_cleanup_at", next_cleanup_time(now, settings.timezone))
        self.db.set_meta("schedule_timezone", settings.timezone)

    def start(self):
        if self.scheduler is None:
            self.scheduler = threading.Thread(target=self._loop, name="scheduler", daemon=True)
            self.scheduler.start()

    def stop(self):
        self.stop_event.set()
        if self.scheduler:
            self.scheduler.join(timeout=5)
        if self.worker:
            self.worker.join(timeout=2)

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:
                # Error text may include credentials from external libraries; log a fixed message.
                log.error("Scheduler iteration failed; will retry in 15 seconds")
            self.stop_event.wait(15)

    def tick(self):
        now = self.clock()
        if now >= self.db.get_meta("next_scrape_at", now):
            self.request_scrape("scheduled")
        elif self.work_lock.acquire(blocking=False):
            try:
                self.process_notifications()
            finally:
                self.work_lock.release()
        if now >= self.db.get_meta("next_cleanup_at", now):
            self.cleanup()

    def request_scrape(self, trigger="manual"):
        if not self.work_lock.acquire(blocking=False):
            return False
        now = self.clock()
        try:
            if now >= self.db.get_meta("next_scrape_at", now):
                self.db.set_meta("next_scrape_at", next_scrape_time(now, self.settings.timezone))
            self.worker = threading.Thread(target=self._run_locked, args=(trigger,), name="scrape", daemon=True)
            self.worker.start()
        except Exception:
            self.work_lock.release()
            raise
        return True

    def _run_locked(self, trigger):
        try:
            self.scrape(trigger)
        finally:
            self.work_lock.release()

    def scrape(self, trigger="manual"):
        started = self.clock()
        with self.db.connect() as db:
            run_id = db.execute("INSERT INTO runs(started_at,trigger,status) VALUES (?,?,'running')", (started, trigger)).lastrowid
        try:
            products = parse_products(self.fetcher(), self.settings.apac_keywords)
            observed = self.clock()
            with self.db.connect() as db:
                db.execute("UPDATE products SET present=0")
                for product in products:
                    old = db.execute("SELECT * FROM products WHERE id=?", (product["id"],)).fetchone()
                    eligible = qualifies(product)
                    event_id = old["active_event_id"] if old else None
                    if not eligible and event_id:
                        db.execute("UPDATE notifications SET status='cancelled' WHERE id=? AND status='pending'", (event_id,))
                        event_id = None
                    if eligible:
                        body = (f"GreenCloud APAC KVM available\n{product['name']}\n"
                                f"Location: {product['location']}\nPrice: $25.00 USD / year\n"
                                f"Stock: {product['stock']} available\n"
                                f"Checked: {datetime.fromtimestamp(observed, ZoneInfo(self.settings.timezone)).isoformat(timespec='seconds')}\n"
                                f"{product['url']}\nAvailability can change before checkout.")
                        if event_id is None:
                            event_id = db.execute("INSERT INTO notifications(product_id,created_at,next_attempt_at,body) VALUES (?,?,?,?)",
                                                  (product["id"], observed, observed, body)).lastrowid
                        else:
                            db.execute("UPDATE notifications SET body=? WHERE id=? AND status='pending'", (body, event_id))
                    db.execute("""INSERT INTO products(id,name,location,apac,price_cents,currency,billing,stock,orderable,url,specs,last_seen_at,eligible,active_event_id,present)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET
                      name=excluded.name,location=excluded.location,apac=excluded.apac,price_cents=excluded.price_cents,
                      currency=excluded.currency,billing=excluded.billing,stock=excluded.stock,orderable=excluded.orderable,
                      url=excluded.url,specs=excluded.specs,last_seen_at=excluded.last_seen_at,eligible=excluded.eligible,
                      active_event_id=excluded.active_event_id,present=1""",
                      tuple(product[k] for k in ("id", "name", "location", "apac", "price_cents", "currency", "billing", "stock", "orderable", "url"))
                      + (json.dumps(product["specs"]), observed, int(eligible), event_id))
                    db.execute("INSERT INTO observations(run_id,product_id,observed_at,stock,price_cents,eligible) VALUES (?,?,?,?,?,?)",
                               (run_id, product["id"], observed, product["stock"], product["price_cents"], int(eligible)))
                # A removed card is unknown, never treated as a confirmed out-of-stock observation.
                db.execute("UPDATE notifications SET status='cancelled' WHERE status='pending' AND product_id IN (SELECT id FROM products WHERE present=0)")
                db.execute("UPDATE products SET active_event_id=NULL WHERE present=0 AND active_event_id IN (SELECT id FROM notifications WHERE status='cancelled')")
                db.execute("UPDATE runs SET finished_at=?,status='ok',product_count=?,apac_count=?,match_count=? WHERE id=?",
                           (observed, len(products), sum(p["apac"] for p in products), sum(qualifies(p) for p in products), run_id))
                self.db.put_meta(db, "last_success_at", observed)
                matches = [p for p in products if qualifies(p)]
                detail = ("\n".join(f"{p['name']} · {p['location']} · {p['stock']} available" for p in matches[:10])
                          if matches else "No $25/year APAC KVM plans are currently available.")
                self.queue_summary(db, run_id, trigger, observed,
                    f"Products checked: {len(products)}\nAPAC plans: {sum(p['apac'] for p in products)}\n"
                    f"Matching $25/year plans in stock: {len(matches)}\n\n{detail}")
            log.info("Scrape %s complete: %s products, %s APAC matches", run_id, len(products), sum(qualifies(p) for p in products))
        except Exception as error:
            from .scraper import ScrapeError
            message = str(error)[:300] if isinstance(error, ScrapeError) else "Scrape or database operation failed; previous stock was preserved"
            with self.db.connect() as db:
                finished = self.clock()
                db.execute("UPDATE runs SET finished_at=?,status='error',error=? WHERE id=?", (finished, message, run_id))
                self.queue_summary(db, run_id, trigger, finished, f"FAILED: {message}\nPrevious stock was preserved.")
            log.warning("Scrape %s failed: %s", run_id, message)
            self.process_notifications()
            return False
        self.process_notifications()
        return True

    def queue_summary(self, db, run_id, trigger, finished, detail):
        if not self.settings.telegram_configured or not self.settings.notify_scrape_summary:
            return
        checked = datetime.fromtimestamp(finished, ZoneInfo(self.settings.timezone)).isoformat(timespec='seconds')
        body = (f"GreenWatch scrape finished · #{run_id}\nType: {trigger}\nCompleted: {checked}\n\n"
                f"{detail}\n\n{SOURCE_URL}")
        db.execute("INSERT INTO notifications(product_id,created_at,next_attempt_at,body) VALUES (?,?,?,?)",
                   (f"Scrape #{run_id}", finished, finished, body[:4000]))

    def process_notifications(self):
        if not self.settings.telegram_configured:
            return
        now = self.clock()
        # Do not send availability claims from old data after downtime or failed scrapes.
        last_success = self.db.get_meta("last_success_at", 0)
        with self.db.connect() as db:
            # Completion reports describe a historical run and can deliver even after a failed scrape.
            pending = db.execute("""SELECT * FROM notifications WHERE product_id LIKE 'Scrape #%'
                AND status='pending' AND next_attempt_at<=?""", (now,)).fetchall()
            latest = db.execute("SELECT status FROM runs ORDER BY id DESC LIMIT 1").fetchone()
            if latest and latest["status"] == "ok" and now - last_success <= 6 * 3600 + 60:
                pending += db.execute("""SELECT n.* FROM notifications n JOIN products p ON p.active_event_id=n.id
                    WHERE n.status='pending' AND n.next_attempt_at<=? AND p.present=1 AND p.eligible=1""", (now,)).fetchall()
        for event in pending:
            attempts = event["attempts"] + 1
            try:
                self.sender(self.settings, event["body"])
            except Exception as error:
                from .telegram import TelegramError
                message = str(error) if isinstance(error, TelegramError) else "Telegram delivery failed"
                message = message.replace(self.settings.telegram_token, "[redacted]")[:300]
                delay = (60, 300, 1800, 3600)[min(attempts - 1, 3)]
                with self.db.connect() as db:
                    db.execute("UPDATE notifications SET status=?,attempts=?,next_attempt_at=?,error=? WHERE id=?",
                               ("failed" if attempts >= 5 else "pending", attempts, now + delay, message, event["id"]))
                log.warning("Telegram notification %s failed (attempt %s)", event["id"], attempts)
            else:
                with self.db.connect() as db:
                    db.execute("UPDATE notifications SET status='sent',attempts=?,sent_at=?,error=NULL WHERE id=?", (attempts, self.clock(), event["id"]))

    def cleanup(self):
        now = self.clock()
        cutoff = now - self.settings.retention_days * 86400
        with self.db.connect() as db:
            removed_runs = db.execute("DELETE FROM runs WHERE started_at<? AND status!='running'", (cutoff,)).rowcount
            removed_alerts = db.execute("""DELETE FROM notifications WHERE created_at<? AND status!='pending'
              AND id NOT IN (SELECT active_event_id FROM products WHERE active_event_id IS NOT NULL)""", (cutoff,)).rowcount
            # Remove catalog entries absent throughout the retention period; keep any active episode state.
            db.execute("DELETE FROM products WHERE present=0 AND last_seen_at<? AND active_event_id IS NULL", (cutoff,))
            self.db.put_meta(db, "last_cleanup", {"at": now, "runs_removed": removed_runs, "alerts_removed": removed_alerts})
            self.db.put_meta(db, "next_cleanup_at", next_cleanup_time(now, self.settings.timezone))
        with self.db.connect() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db.execute("PRAGMA incremental_vacuum")
        log.info("Weekly cleanup complete: removed %s runs, %s alerts", removed_runs, removed_alerts)

    def snapshot(self):
        with self.db.connect() as db:
            products = [dict(r) for r in db.execute("SELECT * FROM products WHERE apac=1 ORDER BY present DESC, location,price_cents,name")]
            runs = [dict(r) for r in db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 40")]
            notifications = [dict(r) for r in db.execute("SELECT id,product_id,created_at,status,attempts,sent_at,error FROM notifications ORDER BY id DESC LIMIT 30")]
            trend = [dict(r) for r in db.execute("SELECT started_at,match_count FROM runs WHERE status='ok' ORDER BY id DESC LIMIT 112")][::-1]
            summary = dict(db.execute("SELECT count(*) total, coalesce(sum(status='ok'),0) successful FROM runs WHERE status!='running'").fetchone())
            alert_counts = {r["status"]: r["count"] for r in db.execute("SELECT status,count(*) count FROM notifications GROUP BY status")}
        for p in products:
            p["specs"] = json.loads(p["specs"])
        return {
            "now": self.clock(), "running": self.work_lock.locked(), "products": products,
            "runs": runs, "notifications": notifications, "trend": trend, "summary": summary,
            "alert_counts": alert_counts, "source_url": SOURCE_URL,
            "last_success_at": self.db.get_meta("last_success_at"),
            "next_scrape_at": self.db.get_meta("next_scrape_at"),
            "next_cleanup_at": self.db.get_meta("next_cleanup_at"),
            "last_cleanup": self.db.get_meta("last_cleanup"),
            "settings": {"timezone": self.settings.timezone, "retention_days": self.settings.retention_days,
                         "telegram_configured": self.settings.telegram_configured,
                         "authentication_enabled": bool(self.settings.dashboard_password)},
        }
