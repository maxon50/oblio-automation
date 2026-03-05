#!/usr/bin/env python3
import argparse
import datetime as dt
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from playwright.sync_api import Page, TimeoutError, sync_playwright

load_dotenv()

OBLIO_EMAIL = os.getenv("OBLIO_EMAIL", "").strip()
OBLIO_PASSWORD = os.getenv("OBLIO_PASSWORD", "").strip()
OBLIO_LOGIN_URL = os.getenv("OBLIO_LOGIN_URL", "https://www.oblio.eu/login").strip()
OBLIO_STRIPE_URL = os.getenv("OBLIO_STRIPE_URL", "https://www.oblio.eu/report/integration_stripe").strip()
TIMEZONE = os.getenv("TIMEZONE", "Europe/Bucharest").strip()
HEADLESS = os.getenv("HEADLESS", "1").strip() == "1"
LOGIN_RETRIES = int(os.getenv("LOGIN_RETRIES", "3").strip())
EMIT_RETRIES = int(os.getenv("EMIT_RETRIES", "2").strip())
RUN_RETRIES = int(os.getenv("RUN_RETRIES", "2").strip())
RETRY_DELAY_SECONDS = int(os.getenv("RETRY_DELAY_SECONDS", "5").strip())
ALERT_ON_SUCCESS = os.getenv("ALERT_ON_SUCCESS", "1").strip() == "1"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Emite facturi in Oblio pentru incasarile Stripe fara factura."
    )
    parser.add_argument(
        "--date",
        default="",
        help="Data tinta: YYYY-MM-DD sau DD.MM.YYYY. Implicit: ieri.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Nu apasa Emite factura, doar afiseaza ce ar procesa.",
    )
    parser.add_argument(
        "--slow-ms",
        type=int,
        default=0,
        help="Delay intre actiuni browser (util la debug).",
    )
    return parser.parse_args()


def target_date(raw: str) -> str:
    if raw:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return dt.date.fromisoformat(raw).strftime("%d.%m.%Y")
        if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", raw):
            return raw
        raise ValueError("Format invalid pentru --date. Foloseste YYYY-MM-DD sau DD.MM.YYYY.")
    tz = ZoneInfo(TIMEZONE)
    return (dt.datetime.now(tz).date() - dt.timedelta(days=1)).strftime("%d.%m.%Y")


def fill_first(page: Page, selectors: list[str], value: str) -> None:
    for selector in selectors:
        loc = page.locator(selector)
        if loc.count() > 0:
            loc.first.fill(value)
            return
    raise RuntimeError(f"Nu am gasit camp pentru selectorii: {selectors}")


def click_first(page: Page, selectors: list[str]) -> None:
    for selector in selectors:
        loc = page.locator(selector)
        if loc.count() > 0:
            loc.first.click()
            return
    raise RuntimeError(f"Nu am gasit buton pentru selectorii: {selectors}")


def send_alert(message: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    body = urllib.parse.urlencode(
        {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15):
            pass
    except Exception as exc:
        print(f"[WARN] Alerta nu a putut fi trimisa: {exc}")


def login(page: Page) -> None:
    last_exc: Exception | None = None
    for attempt in range(1, LOGIN_RETRIES + 1):
        try:
            page.goto(OBLIO_LOGIN_URL, wait_until="domcontentloaded")
            fill_first(page, ["input[type='email']", "input[name='email']", "#email"], OBLIO_EMAIL)
            fill_first(page, ["input[type='password']", "input[name='password']", "#password"], OBLIO_PASSWORD)
            click_first(
                page,
                [
                    "button[type='submit']",
                    "button:has-text('Autentificare')",
                    "button:has-text('Login')",
                    "input[type='submit']",
                ],
            )
            page.wait_for_load_state("networkidle")
            return
        except Exception as exc:
            last_exc = exc
            print(f"[WARN] Login esuat (incercarea {attempt}/{LOGIN_RETRIES}): {exc}")
            if attempt < LOGIN_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)
    if last_exc:
        raise last_exc
    raise RuntimeError("Login esuat fara detalii.")


def open_stripe_report(page: Page) -> None:
    page.goto(OBLIO_STRIPE_URL, wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")
    if page.locator("text=Incasare Stripe").count() == 0 and page.locator("text=Stripe").count() == 0:
        raise RuntimeError("Nu am putut confirma deschiderea paginii Rapoarte > Stripe.")


def has_invoice(cell_text: str) -> bool:
    text = " ".join(cell_text.split())
    return bool(re.search(r"[A-Z]{2,}\d{2,}", text))


def click_emit_for_row(page: Page, row_index: int) -> bool:
    rows = page.locator("table tbody tr")
    row = rows.nth(row_index)
    invoice_cell = row.locator("td").last
    before = invoice_cell.inner_text(timeout=4000)
    if has_invoice(before):
        return False

    menu_button = invoice_cell.locator("button, a").first
    if menu_button.count() == 0:
        return False

    for attempt in range(1, EMIT_RETRIES + 1):
        try:
            menu_button.click()
            page.wait_for_timeout(300)
            emit = page.locator("text=Emite factura")
            if emit.count() == 0:
                return False
            emit.first.click()
            page.wait_for_load_state("networkidle")
            time.sleep(1.0)
            after = invoice_cell.inner_text(timeout=4000)
            if has_invoice(after):
                return True
            print(f"[WARN] Emitere neconfirmata, retry {attempt}/{EMIT_RETRIES} pe randul {row_index + 1}")
        except Exception as exc:
            print(f"[WARN] Eroare emitere, retry {attempt}/{EMIT_RETRIES} pe randul {row_index + 1}: {exc}")
        if attempt < EMIT_RETRIES:
            time.sleep(RETRY_DELAY_SECONDS)
    return False


def _process_once(args: argparse.Namespace, day: str) -> dict[str, int]:
    matched = 0
    created = 0
    already = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=args.slow_ms)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(20000)
        result = {"matched": 0, "created": 0, "already": 0}
        try:
            login(page)
            open_stripe_report(page)

            rows = page.locator("table tbody tr")
            total = rows.count()
            print(f"Randuri detectate: {total}")

            for idx in range(total):
                row = rows.nth(idx)
                text = " ".join(row.inner_text(timeout=4000).split())
                if day not in text:
                    continue
                matched += 1
                invoice_text = row.locator("td").last.inner_text(timeout=4000)
                if has_invoice(invoice_text):
                    already += 1
                    continue
                if args.dry_run:
                    print(f"[DRY-RUN] Ar emite factura pentru randul {idx + 1}")
                    continue

                ok = click_emit_for_row(page, idx)
                if ok:
                    created += 1
                    print(f"[OK] Factura emisa pentru randul {idx + 1}")
                else:
                    print(f"[WARN] Nu am confirmat emiterea pentru randul {idx + 1}")

            result = {"matched": matched, "created": created, "already": already}
            browser.close()
            return result
        except TimeoutError as exc:
            page.screenshot(path="error-timeout.png", full_page=True)
            print(f"Timeout: {exc}. Screenshot salvat: error-timeout.png")
            browser.close()
            raise
        except Exception as exc:
            page.screenshot(path="error-generic.png", full_page=True)
            print(f"Eroare: {exc}. Screenshot salvat: error-generic.png")
            browser.close()
            raise


def run() -> int:
    if not OBLIO_EMAIL or not OBLIO_PASSWORD:
        print("Lipseste OBLIO_EMAIL sau OBLIO_PASSWORD in .env")
        return 2

    args = parse_args()
    try:
        day = target_date(args.date)
    except ValueError as exc:
        print(str(exc))
        return 2

    print(f"Data tinta: {day}")
    print(f"Dry-run: {'da' if args.dry_run else 'nu'}")
    print(f"Retry config: run={RUN_RETRIES}, login={LOGIN_RETRIES}, emit={EMIT_RETRIES}")

    last_exc: Exception | None = None
    for attempt in range(1, RUN_RETRIES + 1):
        try:
            summary = _process_once(args, day)
            print("--- Rezumat ---")
            print(f"Incasari din data tinta: {summary['matched']}")
            print(f"Cu factura deja existenta: {summary['already']}")
            print(f"Facturi emise acum: {summary['created']}")
            if ALERT_ON_SUCCESS:
                send_alert("Facturi cu SUCCES")
            return 0
        except Exception as exc:
            last_exc = exc
            print(f"[WARN] Rulare esuata ({attempt}/{RUN_RETRIES}): {exc}")
            if attempt < RUN_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)

    send_alert("Facturi cu EROARE")
    return 1


if __name__ == "__main__":
    sys.exit(run())
