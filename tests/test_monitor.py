import base64
import json
import re
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from bs4 import BeautifulSoup
from greenwatch.config import Settings
from greenwatch.scraper import ScrapeError, parse_products, qualifies
from greenwatch.service import Monitor, next_cleanup_time, next_scrape_time
from greenwatch.telegram import TelegramError, send_message
from greenwatch.web import create_app

FIXTURE = Path(__file__).parent / 'fixtures' / 'store.html'
HTML = FIXTURE.read_text()
BASE_TIME = datetime(2026, 10, 23, 4, tzinfo=timezone.utc).timestamp()


def stock_html(stock=1, price=None, disabled=False):
    soup = BeautifulSoup(HTML, 'html.parser')
    card = next(c for c in soup.select('.product') if c.select_one('[id$="-name"]').text.strip() == 'BudgetKVMSGDC1-2')
    card.select_one('.qty').string = f'{stock} Available'
    if price:
        card.select_one('.price').string = price
    if disabled:
        card.select_one('a').attrs['class'].append('disabled')
    return str(soup)


class ParserTests(unittest.TestCase):
    def test_actual_store_markup(self):
        products = parse_products(HTML, Settings().apac_keywords)
        self.assertEqual(len(products), 44)
        apac = [p for p in products if p['apac']]
        self.assertEqual(len(apac), 10)
        self.assertEqual(len([p for p in apac if p['price_cents'] == 2500]), 5)
        self.assertFalse(any(qualifies(p) for p in products))
        self.assertEqual({p['location'] for p in apac}, {'Singapore DC1','Singapore DC2','Tokyo, JP IIJ Line','Tokyo, JP Softbank Line','Hong Kong DC2'})

    def test_one_or_more_matches(self):
        for amount in (1, 2, 1000):
            products = parse_products(stock_html(amount), Settings().apac_keywords)
            matches = [p for p in products if qualifies(p)]
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]['stock'], amount)

    def test_exact_price_region_cycle_and_orderability(self):
        products = parse_products(stock_html(1), Settings().apac_keywords)
        match = next(p for p in products if qualifies(p))
        for change in ({'price_cents': 4500}, {'price_cents': 2499}, {'apac':0}, {'billing':'Monthly'}, {'currency':'EUR'}, {'orderable':0}, {'stock':0}):
            self.assertFalse(qualifies(match | change))
        self.assertFalse(any(qualifies(p) for p in parse_products(stock_html(1, disabled=True), Settings().apac_keywords)))

    def test_bad_markup_fails_closed(self):
        for html in ('<html>Access denied</html>', HTML.replace('class="qty"','class="changed"',1), HTML.replace('$25.00 USD', '$25.00 EUR',1)):
            with self.assertRaises(ScrapeError):
                parse_products(html, Settings().apac_keywords)

    def test_semiannual_is_not_annual(self):
        products = parse_products(stock_html(1).replace('Annually','Semi-Annually'), Settings().apac_keywords)
        self.assertTrue(all(p['billing'] == 'Semi-Annually' for p in products))
        self.assertFalse(any(qualifies(p) for p in products))

    def test_location_match_has_word_boundaries(self):
        # 'India' must not misclassify 'Indiana'.
        products = parse_products(HTML.replace('Staten Island, NY','Indiana'), Settings().apac_keywords)
        self.assertFalse(next(p for p in products if p['name']=='BudgetKVMNYC-2')['apac'])


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings = Settings(database=Path(self.directory.name)/'test.sqlite3', telegram_token='123:secret', telegram_chat_id='@test', notify_scrape_summary=False)
        self.now = BASE_TIME
        self.html = stock_html(1)
        self.sent = []
        self.monitor = Monitor(self.settings, fetcher=lambda:self.html, sender=lambda s,body:self.sent.append(body), clock=lambda:self.now)

    def tearDown(self):
        self.monitor.stop()
        self.directory.cleanup()

    def test_initial_alert_dedup_restock_and_restart(self):
        self.assertTrue(self.monitor.scrape())
        self.assertEqual(len(self.sent),1)
        self.monitor.scrape()
        self.assertEqual(len(self.sent),1)
        self.monitor = Monitor(self.settings, fetcher=lambda:self.html, sender=lambda s,b:self.sent.append(b), clock=lambda:self.now)
        self.monitor.scrape()
        self.assertEqual(len(self.sent),1)
        self.html = stock_html(0)
        self.monitor.scrape()
        self.html = stock_html(3)
        self.monitor.scrape()
        self.assertEqual(len(self.sent),2)
        self.assertIn('Stock: 3 available',self.sent[-1])
        self.assertEqual(self.monitor.snapshot()['alert_counts']['sent'],2)

    def test_completion_summary_without_stock_and_failure(self):
        self.monitor.settings=replace(self.settings,notify_scrape_summary=True)
        self.html=stock_html(0)
        self.assertTrue(self.monitor.scrape())
        self.assertEqual(len(self.sent),1)
        self.assertIn('No $25/year APAC KVM plans',self.sent[0])
        self.assertIn('APAC plans: 10',self.sent[0])
        self.assertEqual(self.monitor.snapshot()['runs'][0]['status'],'ok')
        self.monitor.process_notifications()
        self.assertEqual(len(self.sent),1)
        self.html='<html>blocked</html>'
        self.assertFalse(self.monitor.scrape())
        self.assertEqual(len(self.sent),2)
        self.assertIn('FAILED:',self.sent[1])

    def test_completion_report_retries_after_delivery_failure(self):
        self.monitor.settings=replace(self.settings,notify_scrape_summary=True)
        self.html=stock_html(0)
        self.monitor.sender=lambda s,b: (_ for _ in ()).throw(TelegramError('temporary failure'))
        self.monitor.scrape()
        self.assertEqual(self.monitor.snapshot()['notifications'][0]['status'],'pending')
        self.now+=61
        self.monitor.sender=lambda s,b:self.sent.append(b)
        self.monitor.process_notifications()
        self.assertEqual(len(self.sent),1)
        self.assertEqual(self.monitor.snapshot()['notifications'][0]['status'],'sent')

    def test_failed_page_preserves_stock_and_pauses_delivery(self):
        self.monitor.sender = lambda s,b: (_ for _ in ()).throw(TelegramError('temporary failure'))
        self.monitor.scrape()
        self.html='<html>blocked</html>'
        self.assertFalse(self.monitor.scrape())
        self.assertEqual(sum(p['eligible'] for p in self.monitor.snapshot()['products']),1)
        self.now += 70
        self.monitor.sender = lambda s,b:self.sent.append(b)
        self.monitor.process_notifications()
        self.assertEqual(self.sent,[])
        self.html=stock_html(1)
        self.monitor.scrape()
        self.assertEqual(len(self.sent),1)

    def test_delivery_retry_and_max_attempts(self):
        self.monitor.sender = lambda s,b: (_ for _ in ()).throw(TelegramError('temporary failure'))
        self.monitor.scrape()
        self.assertEqual(self.monitor.snapshot()['notifications'][0]['attempts'],1)
        self.monitor.process_notifications()
        self.assertEqual(self.monitor.snapshot()['notifications'][0]['attempts'],1)
        for delay in (61,301,1801,3601):
            self.now += delay
            self.monitor.process_notifications()
        notice=self.monitor.snapshot()['notifications'][0]
        self.assertEqual(notice['status'],'failed')
        self.assertEqual(notice['attempts'],5)
        self.now += 60
        self.monitor.process_notifications()
        self.assertEqual(self.monitor.snapshot()['notifications'][0]['attempts'],5)

    def test_restock_cancels_old_pending_delivery(self):
        self.monitor.sender = lambda s,b: (_ for _ in ()).throw(TelegramError('temporary failure'))
        self.monitor.scrape()
        self.html=stock_html(0)
        self.monitor.scrape()
        self.now += 100
        self.monitor.sender = lambda s,b:self.sent.append(b)
        self.monitor.process_notifications()
        self.assertEqual(self.sent,[])
        self.assertEqual(self.monitor.snapshot()['notifications'][0]['status'],'cancelled')
        self.html=stock_html(1)
        self.monitor.scrape()
        self.assertEqual(len(self.sent),1)

    def test_missing_card_cancels_pending_but_does_not_invent_restock(self):
        self.monitor.scrape()
        soup=BeautifulSoup(self.html,'html.parser')
        next(c for c in soup.select('.product') if c.select_one('[id$="-name"]').text.strip()=='BudgetKVMSGDC1-2').decompose()
        self.html=str(soup)
        self.monitor.scrape()
        self.html=stock_html(1)
        self.monitor.scrape()
        self.assertEqual(len(self.sent),1)

    def test_missing_telegram_keeps_event_pending_without_attempts(self):
        self.monitor.settings = replace(self.settings, telegram_token='',telegram_chat_id='')
        self.monitor.scrape()
        notice=self.monitor.snapshot()['notifications'][0]
        self.assertEqual(notice['status'],'pending')
        self.assertEqual(notice['attempts'],0)
        self.assertEqual(self.sent,[])
        self.monitor.settings=self.settings
        self.monitor.process_notifications()
        self.assertEqual(len(self.sent),1)

    def test_stale_data_does_not_notify(self):
        self.monitor.settings = replace(self.settings, telegram_token='',telegram_chat_id='')
        self.monitor.scrape()
        self.monitor.settings=self.settings
        self.now += 7*3600
        self.monitor.process_notifications()
        self.assertEqual(self.sent,[])

    def test_weekly_cleanup_preserves_active_dedup_state(self):
        self.monitor.scrape()
        self.now += 31*86400
        self.monitor.cleanup()
        self.assertEqual(self.monitor.snapshot()['runs'],[])
        with self.monitor.db.connect() as db:
            self.assertEqual(db.execute('PRAGMA auto_vacuum').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT count(*) FROM observations').fetchone()[0],0)
        self.monitor.scrape()
        self.assertEqual(len(self.sent),1)
        self.assertEqual(self.monitor.snapshot()['last_cleanup']['runs_removed'],1)
        self.assertGreater(self.monitor.snapshot()['next_cleanup_at'],self.now)

    def test_overlap_rejected_and_schedule_not_shifted_by_manual_scrape(self):
        started=threading.Event()
        release=threading.Event()
        def blocked_fetch():
            started.set()
            release.wait(timeout=5)
            return self.html
        self.monitor.fetcher=blocked_fetch
        self.assertTrue(self.monitor.request_scrape())
        self.assertTrue(started.wait(timeout=2))
        scheduled=self.monitor.db.get_meta('next_scrape_at')
        self.assertFalse(self.monitor.request_scrape())
        release.set()
        self.monitor.worker.join(timeout=5)
        self.assertFalse(self.monitor.work_lock.locked())
        self.now += 10
        self.assertTrue(self.monitor.request_scrape())
        self.monitor.worker.join(timeout=5)
        self.assertEqual(self.monitor.db.get_meta('next_scrape_at'),scheduled)

    def test_restart_marks_interrupted_runs_failed(self):
        with self.monitor.db.connect() as db:
            db.execute("INSERT INTO runs(started_at,trigger,status) VALUES (?,'scheduled','running')",(self.now,))
        monitor=Monitor(self.settings,clock=lambda:self.now)
        self.assertEqual(monitor.snapshot()['runs'][0]['status'],'error')

    def test_timezone_change_recomputes_clock_slots(self):
        changed=replace(self.settings,timezone='UTC')
        monitor=Monitor(changed,clock=lambda:self.now)
        self.assertEqual(monitor.db.get_meta('next_scrape_at'),next_scrape_time(self.now,'UTC'))
        self.assertEqual(monitor.db.get_meta('next_cleanup_at'),next_cleanup_time(self.now,'UTC'))

    def test_api_health_csrf_auth_and_secrets(self):
        app=create_app(self.settings,False,self.monitor)
        app.testing=True
        client=app.test_client()
        self.assertEqual(client.get('/healthz').status_code,200)
        self.assertNotIn(b'123:secret',client.get('/api/status').data)
        self.assertEqual(client.post('/api/scrape').status_code,403)
        token=re.search(r'name="csrf-token" content="([^"]+)"',client.get('/').text).group(1)
        response=client.post('/api/scrape',headers={'X-CSRF-Token':token})
        self.assertEqual(response.status_code,202)
        self.monitor.worker.join(timeout=5)
        self.assertEqual(len(client.get('/api/status').json['runs']),1)
        secured=create_app(replace(self.settings,dashboard_password='test-password'),False,self.monitor).test_client()
        self.assertEqual(secured.get('/').status_code,401)
        self.assertEqual(secured.get('/healthz').status_code,200)
        auth='Basic '+base64.b64encode(b'admin:test-password').decode()
        self.assertEqual(secured.get('/api/status',headers={'Authorization':auth}).status_code,200)
        self.assertIn("default-src 'self'",client.get('/').headers['Content-Security-Policy'])

    def test_telegram_payload_and_network_errors_hide_token(self):
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self,*args): return None
            def read(self,*args): return b'{"ok":true,"result":{"message_id":1}}'
        with patch('greenwatch.telegram.urlopen',return_value=FakeResponse()) as mocked:
            send_message(self.settings,'hello')
            request=mocked.call_args.args[0]
            self.assertEqual(json.loads(request.data)['chat_id'],'@test')
            self.assertEqual(json.loads(request.data)['text'],'hello')
        with patch('greenwatch.telegram.urlopen',side_effect=RuntimeError('123:secret')):
            with self.assertRaises(TelegramError) as caught:
                send_message(self.settings,'hello')
            self.assertNotIn('123:secret',str(caught.exception))


class ScheduleTests(unittest.TestCase):
    def test_six_hour_bangkok_clock_slots(self):
        now=datetime(2026,10,23,4,tzinfo=timezone.utc).timestamp() # 11:00 Bangkok
        self.assertEqual(next_scrape_time(now,'Asia/Bangkok'),datetime(2026,10,23,5,tzinfo=timezone.utc).timestamp())
        boundary=next_scrape_time(now,'Asia/Bangkok')
        self.assertEqual(next_scrape_time(boundary,'Asia/Bangkok')-boundary,21600)
        for _ in range(12):
            later=next_scrape_time(now,'Asia/Bangkok')
            self.assertGreater(later,now)
            self.assertLessEqual(later-now,21600)
            now=later

    def test_cleanup_is_sunday_three_am_local(self):
        cleanup=next_cleanup_time(BASE_TIME,'Asia/Bangkok')
        from zoneinfo import ZoneInfo
        local=datetime.fromtimestamp(cleanup,ZoneInfo('Asia/Bangkok'))
        self.assertEqual((local.weekday(),local.hour),(6,3))
        self.assertEqual(next_cleanup_time(cleanup,'Asia/Bangkok')-cleanup,7*86400)


if __name__ == '__main__':
    unittest.main()
