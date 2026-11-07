"""
session_mode.py — keep the live bot running for a whole session
================================================================
Start the "F1 Fantasy Standings" workflow by hand before a session (up to 2 h
early — it waits). One run then stays alive for the session and publishes
live/*.json only at checkpoints:

  Qualifying : end of Q1, end of Q2 (detected from the F1 Fantasy API) →
               session complete → final (+15 min)
  Sprint     : every ~10 laps (estimated: every 15 min) → complete → final (+15 min)
  Race       : every ~10 laps (estimated: every 15 min) → complete → final (+45 min,
               Driver of the Day / pit-stop awards land late)

Signals — all from the F1 Fantasy API (no other source):
  • session status   schedule feed MatchStatus: 1 upcoming, 2 live, 4 complete
  • Q1 / Q2 ended    constructor bonus events appear: "...Q2" after Q1, "...Q3" after Q2
Lap numbers are estimates (F1 Fantasy doesn't publish laps); red flags aren't
visible, so lap estimates drift during long stoppages.

The decision logic (Checkpointer) is pure — feed it (time, signals) and it says
what to publish — so simulate_session_mode.py can test it without network.
"""

from datetime import datetime, timedelta, timezone
import time

RULES = {
    #             estimate every   normal length   settle after complete   time fallback for Q1/Q2 ends
    "qualifying": {"every": None,  "length": 60,   "settle": 15, "q_fallback": [30, 55]},
    "sprint":     {"every": 15,    "length": 40,   "settle": 15, "q_fallback": []},
    "race":       {"every": 15,    "length": 110,  "settle": 45, "q_fallback": []},
}
HARD_LIMIT_HOURS = 3.5        # never run longer than this after the scheduled start
LAPS_PER_MINUTE  = 10 / 15    # ~10 laps every 15 minutes


class Checkpointer:
    """Decides when to publish. Call tick(now, signals) about once a minute.

    signals = {"status": "upcoming" | "live" | "complete" | None,
               "q_parts_done": 0 | 1 | 2 | None}     (None = API not reachable)
    """

    def __init__(self, session_type, start_dt):
        self.kind  = session_type
        self.rule  = RULES.get(session_type, RULES["race"])
        self.start = start_dt
        self.parts_published = 0
        self.next_every  = self.rule["every"]
        self.complete_at = None
        self.done        = False

    def phase(self, now):
        """Short label for the website: Q1/Q2/Q3, ~Lap N, Finished, Final."""
        if self.done:
            return "Final"
        if self.complete_at:
            return "Finished"
        if now < self.start:
            return "Starting soon"
        if self.kind == "qualifying":
            return f"Q{min(self.parts_published + 1, 3)}"
        elapsed = (now - self.start).total_seconds() / 60
        if elapsed > self.rule["length"]:
            return "Running"
        return f"~Lap {max(1, round(elapsed * LAPS_PER_MINUTE))}"

    def tick(self, now, signals):
        reasons = []
        if self.done or now < self.start:
            return reasons
        status = signals.get("status")
        parts  = signals.get("q_parts_done")
        elapsed = (now - self.start).total_seconds() / 60

        # 1. qualifying part ends — from the API; time only if the API is unreachable
        if self.kind == "qualifying" and self.complete_at is None:
            if parts is not None:
                while self.parts_published < min(parts, 2):
                    self.parts_published += 1
                    reasons.append(f"Q{self.parts_published} end")
            else:
                for i, mark in enumerate(self.rule["q_fallback"]):
                    if self.parts_published == i and elapsed >= mark:
                        self.parts_published += 1
                        reasons.append(f"Q{i + 1} end (estimated)")

        # 2. every ~10 laps (sprint / race) — estimated from time
        #    (past the normal length the laps are unknown → "still running")
        every = self.rule["every"]
        if every and self.complete_at is None and status != "complete":
            due = []
            while elapsed >= self.next_every:
                if self.next_every <= self.rule["length"]:
                    due.append(f"~lap {round(self.next_every * LAPS_PER_MINUTE)} (estimated)")
                else:
                    due.append("update — session still running (delays)")
                self.next_every += every
            if due:
                reasons.append(due[-1])          # caught up after a late start → one update

        # 3. session complete
        if self.complete_at is None:
            by_api  = status == "complete"
            by_time = status is None and elapsed >= self.rule["length"]
            if by_api or by_time:
                self.complete_at = now
                if self.kind == "qualifying":
                    for p in range(self.parts_published + 1, 3):
                        reasons.append(f"Q{p} end")
                    self.parts_published = 2
                    reasons.append("Q3 end — qualifying complete")
                else:
                    reasons.append("chequered flag" + ("" if by_api else " (estimated)"))

        # 4. final settle run, then stop
        if self.complete_at and now >= self.complete_at + timedelta(minutes=self.rule["settle"]):
            reasons.append(f"final ({self.rule['settle']} min after complete)")
            self.done = True

        # 5. safety stop
        if not self.done and now >= self.start + timedelta(hours=HARD_LIMIT_HOURS):
            reasons.append("time limit reached")
            self.done = True
        return reasons


def run(session_type, start_dt, publish, signals_fn, *, now_fn=None, sleep_fn=None,
        poll_seconds=60, log=print, on_start=None):
    """Loop until the session is final. publish(reasons, final, phase) does one
    update; signals_fn() returns the signals dict (see Checkpointer)."""
    now_fn   = now_fn or (lambda: datetime.now(timezone.utc))
    sleep_fn = sleep_fn or time.sleep
    cp = Checkpointer(session_type, start_dt)
    log(f"[session mode] {session_type} starting {start_dt:%Y-%m-%d %H:%M} UTC")

    # started early by hand → wait for the start (cheaply)
    while now_fn() < start_dt:
        wait = min(poll_seconds * 5, (start_dt - now_fn()).total_seconds())
        log(f"  waiting for the start ({(start_dt - now_fn()).total_seconds() / 60:.0f} min)")
        sleep_fn(max(wait, 1))

    if on_start:                      # session has begun → status-only update (banner)
        on_start(cp.phase(now_fn()))

    updates, last = 0, None
    while not cp.done:
        now = now_fn()
        sig = signals_fn() or {}
        key = (sig.get("status"), sig.get("q_parts_done"))
        if key != last:
            log(f"  {now:%H:%M} F1 Fantasy: status={sig.get('status')} "
                f"q_parts_done={sig.get('q_parts_done')}")
            last = key
        reasons = cp.tick(now, sig)
        if reasons:
            log(f"  {now:%H:%M} PUBLISH: {', '.join(reasons)}")
            publish(reasons, cp.done, cp.phase(now))
            updates += 1
        if not cp.done:
            sleep_fn(poll_seconds)
    log(f"[session mode] done — {updates} update(s) published")
    return updates
