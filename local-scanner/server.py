#!/usr/bin/env python3
"""Local control server for the 4h crypto scanner + dashboard upgrades.

Endpoints:
  GET  /             -> control panel (new UI with add-coin box + tunnel)
  POST /scan         -> รัน scan_vs_plan.py ทันที คืน JSON {ok, text}
  POST /toggle       -> เปิด/ปิด cron (cron_state.json)
  GET  /state        -> {enabled}
  GET  /watchlist    -> รายชื่อเหรียญใน watchlist.json
  POST /add          -> {symbol:"XXXUSDT"} เพิ่มเข้า watchlist (B)
  POST /remove       -> {symbol:"XXXUSDT"} ลบออก
  GET  /analyze?sym=AAVEUSDT -> {"setup": {...}|null, "price": ...} (B: วิเคราะห์ 1 เหรียญ)
  POST /tunnel/start -> เปิด cloudflared quick tunnel (A)
  POST /tunnel/stop  -> ปิด tunnel (C)
  GET  /tunnel/status -> {url, running}
  POST /shutdown     -> ปิด server + tunnel (C)

รัน: uv run python server.py  (background)
"""
import json, os, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8765
STATE = os.path.join(HERE, "cron_state.json")
WATCH = os.path.join(HERE, "watchlist.json")
CTRL = os.path.join(HERE, "crypto_cron_control.html")
_lock = threading.Lock()

# import scanner helpers
sys.path.insert(0, HERE)
try:
    import scanner_v2 as sc
    _HAS_SC = True
except Exception as e:
    _HAS_SC = False
    _SC_ERR = str(e)

try:
    from tunnel import start_quick, stop as tunnel_stop, status as tunnel_status
    _HAS_TUN = True
except Exception as e:
    _HAS_TUN = False
    _TUN_ERR = str(e)

try:
    from alert_hook import alert as send_alert
    _HAS_ALERT = True
except Exception as e:
    _HAS_ALERT = False
    _ALERT_ERR = str(e)


def run_scan():
    with _lock:
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "scan_vs_plan.py")],
            cwd=HERE, capture_output=True, text=True, timeout=280)
        return r.stdout.strip() or r.stderr.strip() or "(ไม่มีผลลัพธ์)"


def read_state():
    try:
        return json.load(open(STATE)).get("enabled", True)
    except Exception:
        return True

def write_state(on):
    json.dump({"enabled": on, "updated": "auto"}, open(STATE, "w"))

def read_watch():
    try:
        return json.load(open(WATCH))
    except Exception:
        return []

def write_watch(lst):
    json.dump(sorted(set(lst)), open(WATCH, "w"), indent=2)

def analyze(sym):
    """วิเคราะห์ 1 เหรียญ: ลอง base_scan แล้ว dip_scan (4h,1h)"""
    if not _HAS_SC:
        return None
    try:
        tick = {t["symbol"]: t for t in sc.get("/fapi/v1/ticker/24hr")}
        if sym not in tick:
            return {"error": "symbol ไม่มีใน Binance perp"}
        # base
        b = sc.base_scan(sym, tick)
        if b:
            return {"type": "base", "setup": b, "price": b["price"]}
        # dip 4h
        d4 = sc.dip_scan(sym, tick, "4h")
        if d4:
            return {"type": "dip4h", "setup": d4, "price": d4["price"]}
        d1 = sc.dip_scan(sym, tick, "1h")
        if d1:
            return {"type": "dip1h", "setup": d1, "price": d1["price"]}
        return {"type": "none", "price": float(tick[sym]["lastPrice"]),
                "msg": "ไม่เข้าเกณฑ์ base/dip ตอนนี้"}
    except Exception as e:
        return {"error": str(e)}


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def do_GET(self):
        p = urlparse(self.path).path
        q = parse_qs(urlparse(self.path).query)
        if p in ("/", "/index.html"):
            try:
                html = open(CTRL, encoding="utf-8").read()
            except Exception:
                html = "<h1>ไม่พบ control panel</h1>"
            self._send(200, html, "text/html")
        elif p == "/state":
            self._send(200, {"enabled": read_state()})
        elif p == "/watchlist":
            self._send(200, {"watchlist": read_watch()})
        elif p == "/analyze":
            sym = (q.get("sym", [""])[0] or "").upper()
            if not sym:
                self._send(400, {"error": "ระบุ ?sym="})
            else:
                self._send(200, analyze(sym))
        elif p == "/tunnel/status":
            self._send(200, tunnel_status() if _HAS_TUN else {"error": _TUN_ERR})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        p = urlparse(self.path).path
        d = self._body()
        if p == "/scan":
            try:
                out = run_scan()
                # alert ถ้ามี setup ใหม่ (D)
                if _HAS_ALERT and "BASE ผ่าน" in out or "DIP ผ่าน" in out:
                    try:
                        send_alert("🔔 สแกนเจอ setup ใหม่:\n" + out[:1500], sound=True)
                    except Exception:
                        pass
                self._send(200, {"ok": True, "text": out})
            except Exception as e:
                self._send(500, {"ok": False, "error": str(e)})
        elif p == "/toggle":
            cur = read_state(); write_state(not cur)
            self._send(200, {"enabled": not cur})
        elif p == "/add":
            sym = (d.get("symbol") or "").upper().strip()
            if not sym.endswith("USDT"):
                sym = sym + "USDT" if sym else ""
            if sym:
                wl = read_watch(); wl.append(sym); write_watch(wl)
                self._send(200, {"ok": True, "watchlist": read_watch()})
            else:
                self._send(400, {"ok": False, "error": "symbol ว่าง"})
        elif p == "/remove":
            sym = (d.get("symbol") or "").upper().strip()
            wl = [x for x in read_watch() if x != sym]
            write_watch(wl)
            self._send(200, {"ok": True, "watchlist": wl})
        elif p == "/tunnel/start":
            if not _HAS_TUN:
                self._send(500, {"ok": False, "error": _TUN_ERR})
            else:
                named = (d.get("mode") == "named") or d.get("named")
                if named:
                    url, msg = start_named(d.get("name", "hermesdash"), PORT)
                else:
                    url, msg = start_quick(PORT)
                self._send(200, {"ok": bool(url), "url": url, "msg": msg, "mode": "named" if named else "quick"})
        elif p == "/tunnel/stop":
            if _HAS_TUN:
                self._send(200, {"ok": True, "msg": tunnel_stop()})
            else:
                self._send(500, {"error": _TUN_ERR})
        elif p == "/shutdown":
            # ปิด tunnel แล้วปิด server (C)
            if _HAS_TUN:
                try: tunnel_stop()
                except Exception: pass
            threading.Thread(target=lambda: (self.server.shutdown())).start()
            self._send(200, {"ok": True, "msg": "server shutting down"})
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"Control server on http://127.0.0.1:{PORT}")
    srv.serve_forever()
