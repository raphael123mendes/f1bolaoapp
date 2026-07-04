"""
f1_login.py — Auto-login to F1 Fantasy and extract session cookie
==================================================================
Uses Playwright (headless Chromium) to log in via account.formula1.com,
navigates the key fantasy pages, then makes direct API calls (via
page.evaluate / fetch in the browser context) to fully establish the
session before extracting cookies.

USAGE:
  python f1_login.py                          # reads F1_EMAIL / F1_PASSWORD from env
  python f1_login.py --out path/to/cookie.txt # custom output path
  python f1_login.py --no-headless            # show browser (debug)
"""

import os
import sys
import argparse
import json
import time
from pathlib import Path

LOGIN_URL    = "https://account.formula1.com/#/en/login"
FANTASY_HOME = "https://fantasy.formula1.com/en/home"
MY_TEAM_URL  = "https://fantasy.formula1.com/en/my-team"
LEAGUE_ID    = "1692401"
LEAGUE_URL   = f"https://fantasy.formula1.com/en/leagues/leaderboard/private/{LEAGUE_ID}"

BASE_FEEDS   = "https://fantasy.formula1.com/feeds"
BASE_SVC     = "https://fantasy.formula1.com/services/user"


def _wait(page, state="networkidle", timeout=20000):
    """Wait for load state, falling back silently on timeout."""
    try:
        page.wait_for_load_state(state, timeout=timeout)
    except Exception:
        pass


def _goto(page, url, label):
    print(f"  → {label} ...")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        _wait(page, "networkidle", 15000)
    except Exception as e:
        print(f"    ⚠  Navigation warning: {e}")
    print(f"    URL: {page.url}")


def _api_call(page, js_expr, label):
    """Run a JS fetch in the browser context and return the parsed result."""
    try:
        result = page.evaluate(js_expr)
        print(f"    {label}: {result}")
        return result
    except Exception as e:
        print(f"    ⚠  {label} failed: {e}")
        return None


def login(email: str, password: str, out_path: str, headless: bool = True) -> bool:
    try:
        from playwright.sync_api import sync_playwright
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
            # ── 1. Navigate to F1 login ────────────────────────────────────────
            print(f"  Navigating to login page...")
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)  # SPA needs time to render after hash navigation

            # ── 2. Dismiss cookie consent ──────────────────────────────────────
            for sel in [
                "#onetrust-accept-btn-handler",
                "button:has-text('Accept All')",
                "button:has-text('Accept')",
                "button:has-text('Agree')",
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
                'input[type="email"]', 'input[name="email"]', 'input[id="email"]',
                'input[placeholder*="email" i]', 'input[autocomplete="email"]',
                'input[autocomplete="username"]',
            ]:
                try:
                    field = page.locator(sel).first
                    field.wait_for(state="visible", timeout=8000)
                    field.fill(email)
                    email_filled = True
                    print(f"    filled ({sel})")
                    break
                except Exception:
                    pass
            if not email_filled:
                print("ERROR: Could not find email field.")
                _save_debug(page, "debug_email.png")
                return False

            time.sleep(0.5)

            # ── 4. Fill password ───────────────────────────────────────────────
            print("  Filling password...")
            pwd_filled = False
            for sel in [
                'input[type="password"]', 'input[name="password"]',
                'input[id="password"]', 'input[autocomplete="current-password"]',
            ]:
                try:
                    field = page.locator(sel).first
                    field.wait_for(state="visible", timeout=5000)
                    field.fill(password)
                    pwd_filled = True
                    print(f"    filled ({sel})")
                    break
                except Exception:
                    pass
            if not pwd_filled:
                print("ERROR: Could not find password field.")
                _save_debug(page, "debug_password.png")
                return False

            time.sleep(0.5)

            # ── 5. Submit ──────────────────────────────────────────────────────
            print("  Submitting login form...")
            submitted = False
            for sel in [
                'button[type="submit"]', "button:has-text('Sign In')",
                "button:has-text('Log in')", "button:has-text('Login')",
                'input[type="submit"]',
            ]:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=3000):
                        btn.click()
                        submitted = True
                        print(f"    submitted ({sel})")
                        break
                except Exception:
                    pass
            if not submitted:
                page.keyboard.press("Enter")
                print("    submitted via Enter")

            # ── 6. Wait for login redirect ─────────────────────────────────────
            print("  Waiting for login redirect...")
            time.sleep(5)
            _wait(page, "networkidle", 10000)
            print(f"  URL after login: {page.url}")

            if "account.formula1.com" in page.url or "login" in page.url.lower():
                print("ERROR: Still on login page — credentials may be wrong.")
                _save_debug(page, "debug_login_failed.png")
                return False

            # ── 7. Navigate key fantasy pages ─────────────────────────────────
            _goto(page, FANTASY_HOME, "Fantasy Home")
            _save_debug(page, "debug_home.png")

            _goto(page, MY_TEAM_URL, "My Team")
            _save_debug(page, "debug_myteam.png")

            _goto(page, LEAGUE_URL, "League Leaderboard")
            _save_debug(page, "debug_league.png")

            # ── 8. Trigger the /services/user/ API calls via page.evaluate ────
            # Running fetch() inside the browser context means all session
            # cookies are automatically included — no need to click UI elements.
            print("  Triggering API calls to establish full session...")
            buster = str(int(time.time() * 1000))

            # 8a. Fetch leaderboard feed → get first user's GUID
            lb_js = f"""async () => {{
                try {{
                    const r = await fetch(
                        '{BASE_FEEDS}/leaderboard/privateleague/list_1_{LEAGUE_ID}_0_1.json?buster={buster}',
                        {{credentials: 'include'}}
                    );
                    const data = await r.json();
                    const users = (data && data.Value && data.Value.leaderboard) ? data.Value.leaderboard : [];
                    if (!users.length) return {{status: r.status, error: 'leaderboard empty'}};
                    const first = users[0];
                    const guid = first.user_guid || first.id || first.userId || first.guid || '';
                    return {{status: r.status, guid: guid, keys: Object.keys(first).join(','), count: users.length}};
                }} catch(e) {{ return {{error: e.message}}; }}
            }}"""
            lb_result = _api_call(page, lb_js, "Leaderboard feed")

            user_guid = None
            if lb_result and isinstance(lb_result, dict):
                user_guid = lb_result.get("guid", "")
                if user_guid:
                    print(f"  Got user GUID: {user_guid[:8]}...")
                else:
                    print(f"  ⚠  Could not extract GUID. Leaderboard keys: {lb_result.get('keys', 'n/a')}")

            if user_guid:
                # 8b. Call opponentgamedayget (cards endpoint)
                cards_js = f"""async () => {{
                    try {{
                        const r = await fetch(
                            '{BASE_SVC}/opponentteam/opponentgamedayget/1/{user_guid}/1?buster={buster}',
                            {{credentials: 'include'}}
                        );
                        return {{status: r.status, ok: r.ok}};
                    }} catch(e) {{ return {{error: e.message}}; }}
                }}"""
                _api_call(page, cards_js, "opponentgamedayget (cards)")

                # 8c. Call opponentgamedayplayerteamget (picks + budget)
                # Use gameday=1 first to warm up, then try to find current gameday
                picks_js = f"""async () => {{
                    try {{
                        // First try: get schedule to find current gameday
                        let gameday = 1;
                        try {{
                            const sr = await fetch(
                                '{BASE_FEEDS}/session/feed.json?buster={buster}',
                                {{credentials: 'include'}}
                            );
                            if (sr.ok) {{
                                const sd = await sr.json();
                                const gd = sd && sd.Value && sd.Value.CurrentGameday;
                                if (gd) gameday = gd;
                            }}
                        }} catch(_) {{}}

                        const r = await fetch(
                            `{BASE_SVC}/opponentteam/opponentgamedayplayerteamget/1/{user_guid}/1/${{gameday}}/1?buster={buster}`,
                            {{credentials: 'include'}}
                        );
                        return {{status: r.status, ok: r.ok, gameday: gameday}};
                    }} catch(e) {{ return {{error: e.message}}; }}
                }}"""
                picks_result = _api_call(page, picks_js, "opponentgamedayplayerteamget (picks)")

                if picks_result and picks_result.get("status") not in (200, 201):
                    print(f"  ⚠  Picks API returned status {picks_result.get('status')} — cookie may lack /services/user/ scope")

            else:
                print("  ⚠  Skipping service API calls — no user GUID available")
                print("  ℹ  Cookies will still be extracted; they may lack full /services scope")

            # ── 9. Extract all cookies ─────────────────────────────────────────
            cookies = context.cookies()
            if not cookies:
                print("ERROR: No cookies found after login.")
                _save_debug(page, "debug_nocookie.png")
                return False

            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
            key_cookies = {c["name"] for c in cookies}
            print(f"  Extracted {len(cookies)} cookies ({len(cookie_str)} chars)")
            print(f"  Key cookies present: {', '.join(k for k in ['F1_FANTASY_007','reese84','LEAGUE_BUSTER','TEAM_BUSTER','HOME_BUSTER'] if k in key_cookies)}")

            # ── 10. Write cookie.txt ───────────────────────────────────────────
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_text(cookie_str, encoding="utf-8")
            print(f"  Cookie written → {out_path}")
            return True

        except Exception as e:
            print(f"ERROR during login: {e}")
            _save_debug(page, "debug_exception.png")
            return False

        finally:
            browser.close()


def _save_debug(page, filename="debug.png"):
    try:
        path = Path(__file__).parent / filename
        page.screenshot(path=str(path))
        print(f"    Screenshot: {path}")
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

    print(f"\n{'='*55}")
    print(f"  F1 Fantasy Auto-Login")
    print(f"{'='*55}")
    print(f"  Email  : {email[:4]}***")
    print(f"  Output : {out_path}")
    print(f"  League : {LEAGUE_ID}")

    ok = login(email, password, out_path, headless=not args.no_headless)

    if ok:
        print("\n✅ Login successful — cookie ready.")
    else:
        print("\n❌ Login failed — check debug screenshots in bot/ folder.")
        sys.exit(1)


if __name__ == "__main__":
    main()
