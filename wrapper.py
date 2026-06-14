#!/usr/bin/env python3
"""
CF Solver Wrapper -- By 0.P aka @NUGHAboli

Proxies /cloudflare to i.exe (managed externally by the YAML),
tracks solve stats, and serves a live dashboard at /.

Usage:
  python wrapper.py <public_port> <solver_port>
  e.g.  python wrapper.py 8742 8743

Zero external dependencies -- pure stdlib.
"""

import json
import os
import sys
import threading
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import deque
from datetime import datetime

AUTHOR  = "0.P aka @NUGHAboli"
VERSION = "2.1.0"

# ── Shared state ──────────────────────────────────────────────────────────────
_lock  = threading.Lock()
_start = time.time()

_stats: dict = {
    "started_at":    "",
    "total":         0,
    "turnstile":     {"ok": 0, "fail": 0},
    "iuam":          {"ok": 0, "fail": 0},
    "cache_hits":    0,
    "elapsed_sum":   0.0,
    "elapsed_count": 0,
}
_recent: deque = deque(maxlen=100)

# Set by main() before server starts
_solver_port: int = 8743


def _record(mode: str, success: bool, elapsed: str, cached: bool = False) -> None:
    with _lock:
        _stats["total"] += 1
        if cached:
            _stats["cache_hits"] += 1
        bucket = _stats.setdefault(mode, {"ok": 0, "fail": 0})
        bucket["ok" if success else "fail"] += 1
        try:
            secs = float(str(elapsed).rstrip("s"))
            _stats["elapsed_sum"]   += secs
            _stats["elapsed_count"] += 1
        except (ValueError, TypeError):
            pass
        _recent.appendleft({
            "time":    datetime.utcnow().strftime("%H:%M:%S"),
            "mode":    mode,
            "status":  "ok" if success else "fail",
            "elapsed": elapsed,
            "cached":  cached,
        })


def _solver_alive() -> bool:
    try:
        urllib.request.urlopen(
            f"http://127.0.0.1:{_solver_port}/health", timeout=2
        )
        return True
    except Exception:
        pass
    # Fallback: try hitting any route
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{_solver_port}/cloudflare",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2)
        return True
    except urllib.error.HTTPError:
        return True   # got a response — it's alive
    except Exception:
        return False


def _get_stats() -> dict:
    with _lock:
        s  = {k: (v.copy() if isinstance(v, dict) else v) for k, v in _stats.items()}
        ec = s["elapsed_count"]
        s["avg_elapsed"]  = round(s["elapsed_sum"] / ec, 2) if ec else 0.0
        s["uptime_secs"]  = int(time.time() - _start)
        s["solver_alive"] = _solver_alive()
        s["recent"]       = list(_recent)
        return s


# ── Dashboard HTML ─────────────────────────────────────────────────────────────
_DASHBOARD = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CF Solver -- {AUTHOR}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#07070e;color:#ddd;font-family:'Segoe UI',system-ui,monospace;min-height:100vh}}
.hdr{{background:linear-gradient(135deg,#12122a,#0e1e40);padding:22px 28px;border-bottom:1px solid #f7525430}}
.hdr h1{{font-size:1.45rem;font-weight:700;color:#fff;letter-spacing:.5px}}
.hdr .sub{{font-size:.78rem;color:#aaa;margin-top:5px}}
.badge{{display:inline-block;border-radius:4px;padding:2px 9px;font-size:.68rem;margin-left:10px;vertical-align:middle;border:1px solid}}
.badge.live{{background:#00ff8818;border-color:#00ff8860;color:#00ff88}}
.badge.down{{background:#ff335518;border-color:#ff335560;color:#ff3355}}
.body{{padding:22px 28px}}
.rf{{font-size:.7rem;color:#444;text-align:right;margin-bottom:10px}}
#cd{{color:#f75254}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:14px;margin-bottom:20px}}
.card{{background:#10101e;border:1px solid #ffffff0d;border-radius:10px;padding:18px 12px;text-align:center}}
.card .v{{font-size:2rem;font-weight:700;color:#fff}}
.card .v.g{{color:#00ff88}}.card .v.r{{color:#ff3355}}.card .v.b{{color:#4da8ff}}.card .v.y{{color:#ffd700}}
.card .l{{font-size:.68rem;color:#666;margin-top:5px;text-transform:uppercase;letter-spacing:1px}}
.sec{{background:#10101e;border:1px solid #ffffff0d;border-radius:10px;padding:18px;margin-bottom:18px}}
.sec h2{{font-size:.78rem;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:14px}}
table{{width:100%;border-collapse:collapse;font-size:.8rem}}
th{{text-align:left;color:#444;font-weight:500;padding:5px 10px;border-bottom:1px solid #ffffff08}}
td{{padding:6px 10px;border-bottom:1px solid #ffffff05}}
tr:hover td{{background:#ffffff03}}
.ok{{color:#00ff88;font-weight:600}}.fail{{color:#ff3355;font-weight:600}}
.ct{{background:#ffd70018;border:1px solid #ffd70050;color:#ffd700;border-radius:3px;font-size:.65rem;padding:1px 5px}}
footer{{text-align:center;color:#252535;font-size:.7rem;padding:18px}}
</style>
</head>
<body>
<div class="hdr">
  <h1>CF Solver Dashboard <span class="badge" id="sb">...</span></h1>
  <div class="sub">By {AUTHOR} &nbsp;|&nbsp; v{VERSION}</div>
</div>
<div class="body">
  <div class="rf">Auto-refresh in <span id="cd">10</span>s</div>
  <div class="grid">
    <div class="card"><div class="v" id="tot">--</div><div class="l">Total Solves</div></div>
    <div class="card"><div class="v g" id="tso">--</div><div class="l">Turnstile OK</div></div>
    <div class="card"><div class="v r" id="tsf">--</div><div class="l">Turnstile Fail</div></div>
    <div class="card"><div class="v b" id="imo">--</div><div class="l">IUAM OK</div></div>
    <div class="card"><div class="v r" id="imf">--</div><div class="l">IUAM Fail</div></div>
    <div class="card"><div class="v y" id="cch">--</div><div class="l">Cache Hits</div></div>
    <div class="card"><div class="v" id="avg">--</div><div class="l">Avg Elapsed</div></div>
    <div class="card"><div class="v" id="upt">--</div><div class="l">Uptime</div></div>
  </div>
  <div class="sec">
    <h2>Recent Solves (last 100)</h2>
    <table>
      <thead><tr><th>Time (UTC)</th><th>Mode</th><th>Status</th><th>Elapsed</th><th></th></tr></thead>
      <tbody id="tb"></tbody>
    </table>
  </div>
</div>
<footer>CF Solver | By {AUTHOR}</footer>
<script>
function fmt(s){{if(s<60)return s+'s';if(s<3600)return Math.floor(s/60)+'m '+s%60+'s';return Math.floor(s/3600)+'h '+Math.floor(s%3600/60)+'m'}}
function load(){{
  fetch('/stats').then(r=>r.json()).then(s=>{{
    document.getElementById('tot').textContent=s.total;
    document.getElementById('tso').textContent=(s.turnstile||{{}}).ok||0;
    document.getElementById('tsf').textContent=(s.turnstile||{{}}).fail||0;
    document.getElementById('imo').textContent=(s.iuam||{{}}).ok||0;
    document.getElementById('imf').textContent=(s.iuam||{{}}).fail||0;
    document.getElementById('cch').textContent=s.cache_hits;
    document.getElementById('avg').textContent=s.avg_elapsed+'s';
    document.getElementById('upt').textContent=fmt(s.uptime_secs);
    const sb=document.getElementById('sb');
    if(s.solver_alive){{sb.textContent='LIVE';sb.className='badge live'}}
    else{{sb.textContent='DOWN';sb.className='badge down'}}
    document.getElementById('tb').innerHTML=(s.recent||[]).map(r=>
      `<tr><td>${{r.time}}</td><td>${{r.mode}}</td><td class="${{r.status}}">${{r.status.toUpperCase()}}</td><td>${{r.elapsed}}</td><td>${{r.cached?'<span class="ct">CACHED</span>':''}}</td></tr>`
    ).join('');
  }}).catch(()=>{{}});
}}
load();
let c=10;
setInterval(()=>{{c--;document.getElementById('cd').textContent=c;if(c<=0){{c=10;load();}}}},1000);
</script>
</body>
</html>"""


# ── HTTP Handler ───────────────────────────────────────────────────────────────
class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/dashboard"):
            self._send(200, "text/html; charset=utf-8", _DASHBOARD.encode())
        elif path == "/stats":
            self._send(200, "application/json", json.dumps(_get_stats()).encode())
        elif path == "/health":
            body = json.dumps({"ok": True, "uptime": int(time.time() - _start)})
            self._send(200, "application/json", body.encode())
        else:
            self._send(404, "application/json", b'{"message":"Not Found"}')

    def do_POST(self):
        path = self.path.split("?")[0]
        if path != "/cloudflare":
            self._send(404, "application/json", b'{"message":"Not Found"}')
            return

        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)
        ct     = self.headers.get("Content-Type", "application/json")

        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{_solver_port}/cloudflare",
                data=body,
                headers={"Content-Type": ct},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                resp_body = resp.read()
                self._track(body, resp_body)
                self._send(200, "application/json", resp_body)
        except urllib.error.HTTPError as e:
            rb = e.read()
            self._track(body, rb, override_fail=True)
            self._send(e.code, "application/json", rb)
        except Exception as exc:
            err = json.dumps({"code": 502, "message": str(exc)}).encode()
            self._send(502, "application/json", err)

    @staticmethod
    def _track(req_body: bytes, resp_body: bytes, override_fail: bool = False) -> None:
        try:
            orig    = json.loads(req_body)
            mode    = orig.get("mode", "unknown")
            parsed  = json.loads(resp_body)
            code    = parsed.get("code")
            success = (not override_fail) and (code is None or code == 200)
            elapsed = parsed.get("elapsed", "0s")
            cached  = bool(parsed.get("cached", False))
            _record(mode, success, elapsed, cached)
        except Exception:
            pass

    def _send(self, code: int, ct: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


# ── Banner ────────────────────────────────────────────────────────────────────
def _banner(public_port: int, solver_port: int) -> None:
    w = 56
    print("=" * w)
    print(f"  CF Solver  --  By {AUTHOR}")
    print(f"  v{VERSION}")
    print("=" * w)
    print(f"  Solver  (i.exe)  : http://127.0.0.1:{solver_port}")
    print(f"  Wrapper (public) : http://0.0.0.0:{public_port}")
    print(f"  Dashboard        : http://0.0.0.0:{public_port}/")
    print(f"  Endpoint         : http://0.0.0.0:{public_port}/cloudflare")
    print("=" * w)
    print()


# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    global _solver_port

    # Force UTF-8 on Windows (avoids cp1252 UnicodeEncodeError)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    # Args: wrapper.py <public_port> <solver_port>
    public_port  = int(os.environ.get("PUBLIC_PORT",  sys.argv[1] if len(sys.argv) > 1 else "8742"))
    _solver_port = int(os.environ.get("SOLVER_PORT",  sys.argv[2] if len(sys.argv) > 2 else str(public_port + 1)))

    _stats["started_at"] = datetime.utcnow().isoformat() + "Z"

    _banner(public_port, _solver_port)
    print(f"[wrapper] Waiting for i.exe on port {_solver_port} ...")

    # Wait up to 60s for i.exe to start accepting connections
    deadline = time.time() + 60
    while time.time() < deadline:
        if _solver_alive():
            print(f"[wrapper] i.exe is up on port {_solver_port} -- starting proxy")
            break
        time.sleep(2)
    else:
        print(f"[wrapper] WARNING: i.exe not detected on {_solver_port} after 60s, starting anyway")

    server = HTTPServer(("0.0.0.0", public_port), _Handler)
    server.socket.settimeout(1)
    print(f"[wrapper] Dashboard live -> http://0.0.0.0:{public_port}/")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[wrapper] Shutting down...")


if __name__ == "__main__":
    main()
