"""
f1_login.py — Auto-login to F1 Fantasy and extract session cookie
==================================================================
Uses Playwright (headless Chromium) to log in with email + password,
then writes the full cookie string to cookie.txt.

Called at the start of both GitHub Actions workflows so the cookie
is always fresh — no manual DevTools copy needed.

USAGE:
  python f1_login.py                          # reads F1_EMAIL / F1_PASSWORD from env
  python f1_login.py --out path/to/cookie.txt # custom output path
"""

import os
import sys
import argparse
import time
from pathlib import Path


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
            # ── 1. Open F1 Fantasy ─────────────────────────────────────────────
            print("  Navigating to fantasy.formula1.com ...")
            page.goto("https://fantasy.formula1.com/", wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)

            # ── 2. Dismiss cookie consent banner if present ────────────────────
            for sel in [
                "button:has-text('Accept All')",
                "button:has-text('Accept')",
                "button:has-text('Agree')",
                "#onetrust-accept-btn-handler",
            ]:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=3000):
                        btn.click()
                        print("  Dismissed cookie consent banner.")
                        time.sleep(1)
                        break
                except Exception:
                    pass

            # ── 3. Click Sign In ───────────────────────────────────────────────
            print("  Clicking Sign In...")
            signed_in = False
            for sel in [
                "a:has-text('Sign In')",
                "button:has-text('Sign In')",
                "a:has-text('Log in')",
                "button:has-text('Log in')",
                "[data-testid='sign-in']",
            ]:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=4000):
                        btn.click()
                        signed_in = True
                        break
                except Exception:
                    pass

            if not signed_in:
                # Maybe already on a login page
                print("  Sign In button not found — checking if already on login page...")

            # ── 4. Wait for identity.formula1.com ─────────────────────────────
            print("  Waiting for login page...")
            try:
                page.wait_for_url("**/account/**", timeout=15000)
            except PWTimeout:
                pass
            try:
                page.wait_for_url("**/login**", timeout=10000)
            except PWTimeout:
                pass
            time.sleep(2)
            print(f"  Current URL: {page.url}")

            # ── 5. Fill email ──────────────────────────────────────────────────
            print("  Filling email...")
            email_filled = False
            for sel in [
                'input[type="email"]',
                'input[name="email"]',
                'input[id="email"]',
                'input[placeholder*="email" i]',
                'input[autocomplete="email"]',
            ]:
                try:
                    field = page.locator(sel).first
                    if field.is_visible(timeout=4000):
                        field.fill(email)
                        email_filled = True
                        print(f"  Email filled via: {sel}")
                        break
                except Exception:
                    pass

            if not email_filled:
                print("ERROR: Could not find email field.")
                _save_debug(page)
                return False

            time.sleep(0.5)

            # Some flows have a "Continue" button after email before showing password
            for sel in [
                "button:has-text('Continue')",
                "button:has-text('Next')",
                'button[type="submit"]:has-text("Continue")',
            ]:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=2000):
                        btn.click()
                        print("  Clicked Continue after email.")
                        time.sleep(2)
                        break
                except Exception:
                    pass

            # ── 6. Fill password ───────────────────────────────────────────────
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
                    if field.is_visible(timeout=4000):
                        field.fill(password)
                        pwd_filled = True
                        print(f"  Password filled via: {sel}")
                        break
                except Exception:
                    pass

            if not pwd_filled:
                print("ERROR: Could not find password field.")
                _save_debug(page)
                return False

            time.sleep(0.5)

            # ── 7. Submit ──────────────────────────────────────────────────────
            print("  Submitting login form...")
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
                        print(f"  Submitted via: {sel}")
                        break
                except Exception:
                    pass

            if not submitted:
                page.keyboard.press("Enter")
                print("  Submitted via Enter key.")

            # ── 8. Wait for redirect back to F1 Fantasy ───────────────────────
            print("  Waiting for redirect to fantasy.formula1.com...")
            try:
                page.wait_for_url("*fantasy.formula1.com*", timeout=30000)
            except PWTimeout:
                print("  WARNING: Timed out waiting for redirect — continuing anyway.")

            time.sleep(3)
            print(f"  Final URL: {page.url}")

            # ── 9. Extract cookies ─────────────────────────────────────────────
            cookies = context.cookies()
            if not cookies:
                print("ERROR: No cookies found after login.")
                _save_debug(page)
                return False

            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
            print(f"  Extracted {len(cookies)} cookies ({len(cookie_str)} chars)")

            # Basic sanity check — F1 Fantasy session has known cookie names
            known = {"reese84", "bm_sv", "ak_bmsc", "fantasy"}
            found = {c["name"] for c in cookies}
            overlap = known & found
            if not overlap:
                print("  WARNING: Expected F1 session cookies not found — login may have failed.")
                _save_debug(page)
            else:
                print(f"  Session cookies confirmed: {overlap}")

            # ── 10. Write cookie.txt ───────────────────────────────────────────
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_text(cookie_str, encoding="utf-8")
            print(f"  Cookie written to: {out_path}")
            return True

        except Exception as e:
            print(f"ERROR during login: {e}")
            _save_debug(page)
            return False

        finally:
            browser.close()


def _save_debug(page):
    """Save a screenshot for debugging failed logins."""
    try:
        path = Path(__file__).parent / "login_debug.png"
        page.screenshot(path=str(path))
        print(f"  Debug screenshot saved: {path}")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="F1 Fantasy auto-login")
    parser.add_argument("--out", default=None, help="Output path for cookie.txt")
    parser.add_argument("--no-headless", action="store_true", help="Show browser window (debug)")
    args = parser.parse_args()

    email    = os.environ.get("F1_EMAIL", "").strip()
    password = os.environ.get("F1_PASSWORD", "").strip()

    if not email or not password:
        print("ERROR: F1_EMAIL and F1_PASSWORD environment variables must be set.")
        sys.exit(1)

    # Default output: cookie.txt next to this script
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
        print("\n❌ Login failed — check login_debug.png for screenshot.")
        sys.exit(1)


if __name__ == "__main__":
    main()
