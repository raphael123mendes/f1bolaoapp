"""
run_all.py — F1 Fantasy Master Pipeline
=========================================
Runs all pipeline steps in order, checks each one succeeded before
continuing, and writes a detailed log file.

Steps:
  1. f1_fantasy_league.py   → fetch league standings → race_NN_*.json
  2. f1_price_tracker.py    → fetch price changes   → prices_master.json
  3. f1_teamvalue_tracker.py→ fetch team values     → teamvalue_master.json
  4. f1_image_card.py       → generate standings PNG
  5. f1_whatsapp_sender.py  → send image via WhatsApp (optional)

USAGE:
  python run_all.py              # run all steps
  python run_all.py --no-whatsapp  # skip WhatsApp send
  python run_all.py --image-only   # only regenerate image (skip fetching)

LOGS:
  Written to SCRIPT_DIR/logs/run_YYYY-MM-DD_HH-MM.log
  Also printed to console in real time.
"""

import os
import sys
import time
import argparse
import traceback
import importlib
from datetime import datetime, timezone

from f1_config import cfg, SCRIPT_DIR

# ══════════════════════════════════════════════════════════════════
#  LOG SETUP
# ══════════════════════════════════════════════════════════════════

LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

_log_path   = None
_log_handle = None


def _open_log():
    global _log_path, _log_handle
    ts        = datetime.now().strftime("%Y-%m-%d_%H-%M")
    _log_path = os.path.join(LOG_DIR, f"run_{ts}.log")
    _log_handle = open(_log_path, "w", encoding="utf-8")


def _close_log():
    if _log_handle:
        _log_handle.close()


def log(msg="", level="INFO"):
    """Print to console and write to log file simultaneously."""
    ts   = datetime.now(timezone.utc).strftime("%H:%M:%S")
    line = f"[{ts}] [{level:<5}] {msg}"
    print(line)
    if _log_handle:
        _log_handle.write(line + "\n")
        _log_handle.flush()


def log_divider(char="─", width=62):
    line = char * width
    print(line)
    if _log_handle:
        _log_handle.write(line + "\n")
        _log_handle.flush()


def log_section(title):
    log_divider("═")
    log(f"  {title}")
    log_divider("═")


# ══════════════════════════════════════════════════════════════════
#  STEP RUNNER
# ══════════════════════════════════════════════════════════════════

class StepResult:
    def __init__(self, name, success, duration, error=None):
        self.name     = name
        self.success  = success
        self.duration = duration
        self.error    = error

    def __str__(self):
        status = "✅ OK" if self.success else "❌ FAIL"
        return f"{status}  {self.name:<35} ({self.duration:.1f}s)"


def run_step(step_name, module_name, stop_on_fail=True):
    """
    Import and run the `run()` function from a pipeline module.
    Returns StepResult. If stop_on_fail and step fails, exits the pipeline.
    """
    log()
    log_divider()
    log(f"STEP: {step_name}")
    log_divider()

    t0 = time.time()
    try:
        # Fresh import each time (avoids stale state between steps)
        if module_name in sys.modules:
            mod = importlib.reload(sys.modules[module_name])
        else:
            mod = importlib.import_module(module_name)

        if not hasattr(mod, "run"):
            raise AttributeError(f"Module '{module_name}' has no run() function")

        mod.run()
        duration = time.time() - t0
        log()
        log(f"Step completed in {duration:.1f}s", level="OK")
        return StepResult(step_name, True, duration)

    except SystemExit as e:
        duration = time.time() - t0
        msg = f"Step called sys.exit({e.code})"
        log(msg, level="WARN")
        # Treat exit(0) as success, anything else as failure
        success = (e.code == 0 or e.code is None)
        return StepResult(step_name, success, duration, error=msg if not success else None)

    except Exception:
        duration = time.time() - t0
        tb = traceback.format_exc()
        log(f"Step FAILED after {duration:.1f}s", level="ERROR")
        for line in tb.splitlines():
            log(f"  {line}", level="ERROR")
        result = StepResult(step_name, False, duration, error=tb)
        if stop_on_fail:
            return result   # caller will handle abort
        return result


# ══════════════════════════════════════════════════════════════════
#  SUMMARY
# ══════════════════════════════════════════════════════════════════

def print_summary(results, total_time):
    log()
    log_section("PIPELINE SUMMARY")
    for r in results:
        log(str(r), level="OK" if r.success else "ERROR")
    log_divider()
    failed = [r for r in results if not r.success]
    log(f"  Total time : {total_time:.1f}s")
    log(f"  Steps OK   : {len(results) - len(failed)}/{len(results)}")
    if failed:
        log(f"  Failed     : {', '.join(r.name for r in failed)}", level="ERROR")
    else:
        log("  All steps completed successfully 🏎️", level="OK")
    log_divider()
    if _log_path:
        log(f"  Log saved  : {_log_path}")


# ══════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="F1 Fantasy Pipeline Runner")
    parser.add_argument("--no-whatsapp",  action="store_true", help="Skip WhatsApp send step")
    parser.add_argument("--image-only",   action="store_true", help="Only run image generation (skip data fetch)")
    parser.add_argument("--no-image",     action="store_true", help="Skip image generation and WhatsApp")
    parser.add_argument("--race",         type=int, default=None, metavar="N", help="Override round/race number (e.g. --race 3)")
    args = parser.parse_args()

    # Inject race override into shared cfg so all modules pick it up
    if args.race is not None:
        cfg["matchday_override"] = args.race
        log(f"  Race override: Round {args.race} (--race flag)")


    _open_log()
    pipeline_start = time.time()

    log_section("F1 FANTASY PIPELINE  —  " + datetime.now().strftime("%Y-%m-%d %H:%M"))
    log(f"  League     : {cfg.get('league_name', '?')}  (ID {cfg.get('league_id', '?')})")
    log(f"  Save folder: {cfg.get('save_folder', '?')}")
    log(f"  WhatsApp   : {'enabled' if cfg.get('enable_whatsapp') and not args.no_whatsapp else 'disabled'}")
    log(f"  Image      : {'enabled' if cfg.get('enable_image') and not args.no_image else 'disabled'}")
    log(f"  Mode       : {'image-only' if args.image_only else 'full pipeline'}")

    results = []

    # ── Step 1: Fetch league standings ────────────────────────────
    if not args.image_only:
        r = run_step("Fetch League Standings", "f1_fantasy_league")
        results.append(r)
        if not r.success:
            log("⛔ Aborting pipeline — standings fetch failed.", level="ERROR")
            print_summary(results, time.time() - pipeline_start)
            _close_log()
            sys.exit(1)

    # ── Step 2: Fetch price changes ───────────────────────────────
    if not args.image_only:
        r = run_step("Fetch Price Changes", "f1_price_tracker")
        results.append(r)
        if not r.success:
            log("⚠  Price fetch failed — continuing without price data.", level="WARN")

    # ── Step 3: Fetch team values ─────────────────────────────────
    if not args.image_only:
        r = run_step("Fetch Team Values", "f1_teamvalue_tracker")
        results.append(r)
        if not r.success:
            log("⚠  Team value fetch failed — continuing.", level="WARN")

    # ── Step 4: Generate image ────────────────────────────────────
    if not args.no_image and cfg.get("enable_image", True):
        r = run_step("Generate Standings Image", "f1_image_card")
        results.append(r)
        if not r.success:
            log("⚠  Image generation failed — skipping WhatsApp send.", level="WARN")
            print_summary(results, time.time() - pipeline_start)
            _close_log()
            sys.exit(1)
    else:
        log("  [SKIP] Image generation disabled.")

    # ── Step 5: Send WhatsApp ─────────────────────────────────────
    if not args.no_whatsapp and cfg.get("enable_whatsapp", True) and not args.no_image:
        # Check sender script exists before trying
        sender_path = os.path.join(SCRIPT_DIR, "f1_whatsapp_sender.py")
        if os.path.exists(sender_path):
            r = run_step("Send WhatsApp", "f1_whatsapp_sender", stop_on_fail=False)
            results.append(r)
            if not r.success:
                log("⚠  WhatsApp send failed — image was saved to Drive anyway.", level="WARN")
        else:
            log("  [SKIP] f1_whatsapp_sender.py not found — skipping WhatsApp.", level="WARN")
    else:
        log("  [SKIP] WhatsApp send disabled.")

    # ── Summary ───────────────────────────────────────────────────
    print_summary(results, time.time() - pipeline_start)
    _close_log()

    # Exit with error code if any critical step failed
    failed_critical = [r for r in results if not r.success and "WhatsApp" not in r.name]
    sys.exit(1 if failed_critical else 0)


if __name__ == "__main__":
    main()
