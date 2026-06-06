"""
f1_login.py — Auto-login to F1 Fantasy and extract session cookie
==================================================================
Uses Playwright (headless Chromium) to log in via account.formula1.com,
then navigates to the league leaderboard to build a comprehensive cookie,
and writes the full cookie string to cookie.txt.

USAGE:
  python f1_login.py                          # reads F1_EMAIL / F1_PASSWORD from env
  python f1_login.py --out path/to/cookie.txt # custom output path
"""

import os
import sys
import argparse
import time
from pathlib import Path

LOGIN_URL     = "https://account.formula1.com/#/en/login"
LEAGUE_URL    = "https://fantasy.formula1.com/en/leagues/leaderboard/private/1692401"
FANTASY_BASE  = "https://fantasy.formula1.com"


def login(email: str, password: str, out_path: str, headless: bool = True) -> bool:
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        print("ERROR: playwright not installed — run: pip install playwright")
        return False

    print(f"  Launching browser (headless={headless})...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        try:
            # ── 1. Go directly to F1 login page ───────────────────────────────
            print(f"  Navigating to {LOGIN_URL} ...")
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)  # SPA needs time to render after hash navigation

            # ── 2. Dismiss cookie consent if present ──────────────────────────
            for sel in [
                "button:has-text('Accept All')",
                "button:has-text('Accept')",
                "button:has-text('Agree')",
                "#onetrust-accept-btn-handler",
                ".cookie-accept",
            ]:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=2000):
                        btn.click()
                        print("  Dismissed cookie consent.")
                        time.sleep(1)
                        break
                except Exception:
                    pass

            # ── 3. Fill email ──────────────────────────────────────────────────
            print("  Filling email...")
            email_filled = False
            for sel in [
                'input[type="email"]',
                'input[name="email"]',
                'input[id="email"]',
                'input[placeholder*="email" i]',
                'input[autocomplete="email"]',
                'input[autocomplete="username"]',
            ]:
                try:
                    field = page.locator(sel).first
                    field.wait_for(state="visible", timeout=8000)
                    field.fill(email)
                    email_filled = True
                    print(f"  Email filled ({sel})")
                    break
                except Exception:
                    pass

            if not email_filled:
                print("ERROR: Could not find email field.")
                _save_debug(page, "login_debug_email.png")
                return False

            time.sleep(0.5)

            # ── 4. Fill password ───────────────────────────────────────────────
            print("  Filling password...")
            pwd_filled = False
            for sel in [
                'input[type="password"]',
                'input[name="password"]',
                'input[id="password"]',
                'input[autocomplete="current-password"]',
            ]:
                try:
                    field = page.locator(sel).first
                    field.wait_for(state="visible", timeout=5000)
                    field.fill(password)
                    pwd_filled = True
                    print(f"  Password filled ({sel})")
                    break
                except Exception:
                    pass

            if not pwd_filled:
                print("ERROR: Could not find password field.")
                _save_debug(page, "login_debug_password.png")
                return False

            time.sleep(0.5)

            # ── 5. Submit ──────────────────────────────────────────────────────
            print("  Submitting...")
            submitted = False
            for sel in [
                'button[type="submit"]',
                "button:has-text('Sign In')",
                "button:has-text('Log in')",
                "button:has-text('Login')",
                'input[type="submit"]',
            ]:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=3000):
                        btn.click()
                        submitted = True
                        print(f"  Submitted ({sel})")
                        break
                except Exception:
                    pass

            if not submitted:
                page.keyboard.press("Enter")
                print("  Submitted via Enter.")

            # ── 6. Wait for login to complete ──────────────────────────────────
            print("  Waiting for login to complete...")
            time.sleep(5)
            print(f"  URL after login: {page.url}")

            # ── 7. Navigate to league leaderboard to build full cookie ─────────
            print(f"  Navigating to league leaderboard...")
            page.goto(LEAGUE_URL, wait_until="domcontentloaded", timeout=30000)
            time.sleep(4)
            print(f"  Final URL: {page.url}")

            # Verify we landed on fantasy (not back on login)
            if "account.formula1.com" in page.url or "login" in page.url.lower():
                print("ERROR: Still on login page — credentials may be wrong.")
                _save_debug(page, "login_debug_failed.png")
                return False

            # ── 8. Extract cookies ─────────────────────────────────────────────
            cookies = context.cookies()
            if not cookies:
                print("ERROR: No cookies found.")
                _save_debug(page, "login_debug_nocookie.png")
                return False

            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
            print(f"  Extracted {len(cookies)} cookies ({len(cookie_str)} chars)")

            # ── 9. Write cookie.txt ────────────────────────────────────────────
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_text(cookie_str, encoding="utf-8")
            print(f"  Cookie written → {out_path}")
            return True

        except Exception as e:
            print(f"ERROR during login: {e}")
            _save_debug(page, "login_debug_exception.png")
            return False

        finally:
            browser.close()


def _save_debug(page, filename="login_debug.png"):
    try:
        path = Path(__file__).parent / filename
        page.screenshot(path=str(path))
        print(f"  Debug screenshot: {path}")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="F1 Fantasy auto-login")
    parser.add_argument("--out", default=None, help="Output path for cookie.txt")
    parser.add_argument("--no-headless", action="store_true", help="Show browser (debug)")
    args = parser.parse_args()

    email    = os.environ.get("F1_EMAIL", "").strip()
    password = os.environ.get("F1_PASSWORD", "").strip()

    if not email or not password:
        print("ERROR: F1_EMAIL and F1_PASSWORD environment variables must be set.")
        sys.exit(1)

    out_path = args.out or str(Path(__file__).parent / "cookie.txt")

    print(f"\n{'='*50}")
    print(f"F1 Fantasy Auto-Login")
    print(f"{'='*50}")
    print(f"  Email  : {email[:4]}***")
    print(f"  Output : {out_path}")

    ok = login(email, password, out_path, headless=not args.no_headless)

    if ok:
        print("\n✅ Login successful — cookie ready.")
    else:
        print("\n❌ Login failed — check debug screenshot in bot/ folder.")
        sys.exit(1)


if __name__ == "__main__":
    main()
