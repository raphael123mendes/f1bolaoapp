"""
livetiming_test.py — can the bot read F1's live timing basics from GitHub?
==========================================================================
Connects to F1's live timing feed (SignalR Core, the same feed the official
live timing page uses), subscribes to a few small topics and prints a summary:

  SessionInfo / SessionStatus / TrackStatus   session, started / red flag / finished
  TimingData.SessionPart                      1 = Q1, 2 = Q2, 3 = Q3
  LapCount                                    current / total laps (sprint, race)
  RaceControlMessages                         latest flag / message

It tries twice:
  1. anonymous   — no login at all
  2. with cookie — the F1 Fantasy cookie already saved as the F1_COOKIE secret
Nothing is saved; no cookie or token value is ever printed.

    LT_SECONDS=120 python bot/livetiming_test.py
"""
import json
import os
import time

import requests
import websocket   # pip install websocket-client

HOST   = "livetiming.formula1.com"
RS     = "\x1e"    # SignalR Core record separator
TOPICS = ["SessionInfo", "SessionStatus", "TrackStatus", "LapCount",
          "TimingData", "RaceControlMessages", "Heartbeat"]
PART   = {1: "Q1", 2: "Q2", 3: "Q3"}


def summarise(state, counts):
    info  = state.get("SessionInfo", {})
    lap   = state.get("LapCount", {})
    part  = state.get("TimingData", {}).get("SessionPart")
    msgs  = state.get("RaceControlMessages", {}).get("Messages", [])
    if isinstance(msgs, dict):            # incremental updates arrive as {"12": {...}}
        msgs = list(msgs.values())
    last  = msgs[-1] if msgs else {}
    lines = [
        f"    session      : {info.get('Meeting', {}).get('Name', '?')} — {info.get('Name', '?')}",
        f"    status       : {state.get('SessionStatus', {}).get('Status', '?')}",
        f"    track        : {state.get('TrackStatus', {}).get('Message', '?')}",
        f"    Q part       : {PART.get(part, part if part is not None else '-')}",
        f"    lap          : {lap.get('CurrentLap', '-')} / {lap.get('TotalLaps', '-')}",
        f"    last message : {last.get('Utc', '')} {last.get('Message', '-')}",
        f"    live updates : {dict(counts) or 'none'}",
    ]
    return "\n".join(lines)


def merge(dst, src):
    """Apply an incremental SignalR update (nested dicts) to the stored state."""
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            merge(dst[k], v)
        else:
            dst[k] = v


def attempt(label, extra_cookie=None, seconds=60):
    print(f"\n=== {label} ===")
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0"
    if extra_cookie:
        s.headers["Cookie"] = extra_cookie
    try:
        r = s.post(f"https://{HOST}/signalrcore/negotiate?negotiateVersion=1", timeout=15)
    except Exception as e:
        print(f"  negotiate failed: {e}")
        return False
    print(f"  negotiate: HTTP {r.status_code}")
    if r.status_code != 200:
        return False
    token = r.json().get("connectionToken")

    # cookies for the websocket: load-balancer cookies from negotiate (+ optional login cookie)
    jar = "; ".join(f"{c.name}={c.value}" for c in s.cookies)
    cookie = "; ".join(x for x in (jar, extra_cookie) if x)
    print(f"  load-balancer cookies: {[c.name for c in s.cookies] or 'none'}")

    try:
        ws = websocket.create_connection(
            f"wss://{HOST}/signalrcore?id={requests.utils.quote(token, safe='')}",
            header=[f"Cookie: {cookie}"] if cookie else [], timeout=20)
    except Exception as e:
        print(f"  websocket refused: {e}")
        return False
    print("  websocket: connected")
    ws.send(json.dumps({"protocol": "json", "version": 1}) + RS)
    ws.send(json.dumps({"type": 1, "invocationId": "1", "target": "Subscribe",
                        "arguments": [TOPICS]}) + RS)

    state, counts, got_snapshot = {}, {}, False
    end = time.time() + seconds
    ws.settimeout(5)
    while time.time() < end:
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            continue
        except Exception as e:
            print(f"  connection closed: {e}")
            break
        for part in str(raw).split(RS):
            if not part:
                continue
            msg = json.loads(part)
            if msg.get("type") == 3:                        # reply to Subscribe: full snapshot
                if msg.get("error"):
                    print(f"  subscribe refused: {msg['error']}")
                    ws.close()
                    return False
                got_snapshot = True
                for topic, data in (msg.get("result") or {}).items():
                    if isinstance(data, dict):
                        state[topic] = data
                print(f"  snapshot topics: {sorted(state)}")
            elif msg.get("type") == 1 and msg.get("target") == "feed":   # live update
                topic, data = msg["arguments"][0], msg["arguments"][1]
                counts[topic] = counts.get(topic, 0) + 1
                if isinstance(data, dict):
                    merge(state.setdefault(topic, {}), data)
            elif msg.get("type") == 7:                       # server closed the connection
                print(f"  server closed: {msg.get('error', '')}")
                end = 0
    ws.close()
    print(summarise(state, counts))
    ok = got_snapshot and bool(state.get("SessionStatus"))
    print(f"  RESULT: {'✅ works' if ok else '❌ no usable data'}")
    return ok


if __name__ == "__main__":
    secs = int(os.environ.get("LT_SECONDS", "60"))
    anon = attempt("1) anonymous — no login", seconds=secs)
    cookie = os.environ.get("F1_COOKIE", "").strip()
    with_cookie = attempt("2) with the F1 Fantasy cookie (F1_COOKIE)", cookie, secs) if cookie \
        else print("\n=== 2) skipped — F1_COOKIE not set ===")
    print("\nSUMMARY:",
          "anonymous works ✅ — no credentials needed" if anon else
          "anonymous ❌; " + ("F1_COOKIE works ✅" if with_cookie else "F1_COOKIE ❌ too"))
