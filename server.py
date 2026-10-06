#!/usr/bin/env python3
"""Claude Code session viewer.

Browse every Claude Code session stored under ~/.claude/projects, read the full
conversation, and move selected sessions to the macOS Trash.

Runs only on 127.0.0.1. Every API call must carry the random token generated at
startup, so other websites open in the browser cannot read or delete sessions.
"""

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

CLAUDE = Path.home() / ".claude"
PROJECTS = CLAUDE / "projects"
TRASH = Path.home() / ".Trash"
ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MAX_TEXT = 20000  # per tool result / long block, characters

HERE = Path(__file__).resolve().parent
TOKEN = secrets.token_urlsafe(24)

_cache = {}  # path -> (mtime, size, summary)
_cache_lock = threading.Lock()


# ---------- reading sessions ----------

def running_ids():
    ids = set()
    for f in (CLAUDE / "sessions").glob("*.json"):
        try:
            d = json.loads(f.read_text())
            os.kill(int(d["pid"]), 0)
            ids.add(d["sessionId"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return ids


def iter_records(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def is_noise(text):
    t = text.lstrip()
    return not t or t.startswith("<") or t.startswith("Caveat:")


def summarize(path):
    title = custom = None
    first = None
    cwd = None
    start = None
    n_user = n_asst = 0
    for r in iter_records(path):
        t = r.get("type")
        if t == "ai-title":
            title = r.get("aiTitle") or title
        elif t == "custom-title":
            custom = r.get("customTitle") or custom
        elif t in ("user", "assistant"):
            if cwd is None and r.get("cwd"):
                cwd = r["cwd"]
            if start is None and r.get("timestamp"):
                start = r["timestamp"]
            if t == "assistant":
                n_asst += 1
            elif not r.get("isMeta"):
                content = (r.get("message") or {}).get("content")
                txt = text_of(content)
                if isinstance(content, str) or (isinstance(content, list) and any(b.get("type") == "text" for b in content if isinstance(b, dict))):
                    if not is_noise(txt):
                        n_user += 1
                        if first is None:
                            first = txt.strip()[:200]
    return {
        "title": custom or title,
        "first": first,
        "cwd": cwd,
        "start": start,
        "userTurns": n_user,
        "assistantTurns": n_asst,
    }


def dir_size(p):
    total = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def list_sessions():
    running = running_ids()
    out = []
    for f in PROJECTS.glob("*/*.jsonl"):
        sid = f.stem
        if not ID_RE.match(sid):
            continue
        st = f.stat()
        key = str(f)
        with _cache_lock:
            hit = _cache.get(key)
        if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
            s = hit[2]
        else:
            s = summarize(f)
            with _cache_lock:
                _cache[key] = (st.st_mtime, st.st_size, s)
        side = f.parent / sid
        size = st.st_size + (dir_size(side) if side.is_dir() else 0)
        subagents = len(list((side / "subagents").glob("*.jsonl"))) if side.is_dir() else 0
        out.append({
            "id": sid,
            "project": f.parent.name,
            "cwd": s["cwd"] or f.parent.name,
            "title": s["title"],
            "first": s["first"],
            "start": s["start"],
            "updated": datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(),
            "size": size,
            "userTurns": s["userTurns"],
            "assistantTurns": s["assistantTurns"],
            "subagents": subagents,
            "running": sid in running,
        })
    out.sort(key=lambda x: x["updated"], reverse=True)
    return out


def clip(s):
    if len(s) > MAX_TEXT:
        return s[:MAX_TEXT] + f"\n… ({len(s) - MAX_TEXT:,} 文字省略)"
    return s


def find_session_file(sid):
    for f in PROJECTS.glob(f"*/{sid}.jsonl"):
        return f
    return None


def load_conversation(path):
    msgs = []
    for r in iter_records(path):
        t = r.get("type")
        if t not in ("user", "assistant"):
            continue
        content = (r.get("message") or {}).get("content")
        blocks = []
        if isinstance(content, str):
            kind = "meta" if (r.get("isMeta") or is_noise(content)) else "text"
            blocks.append({"kind": kind, "text": clip(content)})
        elif isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text":
                    kind = "meta" if (r.get("isMeta") or (t == "user" and is_noise(b.get("text", "")))) else "text"
                    blocks.append({"kind": kind, "text": clip(b.get("text", ""))})
                elif bt == "thinking":
                    if b.get("thinking"):
                        blocks.append({"kind": "thinking", "text": clip(b["thinking"])})
                elif bt == "tool_use":
                    blocks.append({"kind": "tool_use", "name": b.get("name", "?"),
                                   "text": clip(json.dumps(b.get("input"), ensure_ascii=False, indent=2))})
                elif bt == "tool_result":
                    c = b.get("content")
                    if isinstance(c, list):
                        parts = []
                        for x in c:
                            if isinstance(x, dict) and x.get("type") == "text":
                                parts.append(x.get("text", ""))
                            elif isinstance(x, dict) and x.get("type") == "image":
                                parts.append("[画像]")
                        c = "\n".join(parts)
                    blocks.append({"kind": "tool_result", "error": bool(b.get("is_error")),
                                   "text": clip(c if isinstance(c, str) else json.dumps(c, ensure_ascii=False))})
                elif bt == "image":
                    blocks.append({"kind": "text", "text": "[画像]"})
        if blocks:
            msgs.append({"role": t, "time": r.get("timestamp"), "sidechain": bool(r.get("isSidechain")), "blocks": blocks})
    return msgs


# ---------- deleting ----------

def delete_sessions(ids):
    running = running_ids()
    dest = TRASH / f"claude-sessions-{datetime.now():%Y%m%d-%H%M%S}"
    results = []
    for sid in ids:
        if not isinstance(sid, str) or not ID_RE.match(sid):
            results.append({"id": sid, "ok": False, "reason": "不正なID"})
            continue
        if sid in running:
            results.append({"id": sid, "ok": False, "reason": "実行中"})
            continue
        targets = list(PROJECTS.glob(f"*/{sid}.jsonl")) + [p for p in PROJECTS.glob(f"*/{sid}") if p.is_dir()]
        targets += [p for p in (CLAUDE / "file-history" / sid, CLAUDE / "session-env" / sid) if p.exists()]
        moved = 0
        for p in targets:
            d = dest / sid
            d.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(d / f"{p.parent.name}__{p.name}"))
            moved += 1
        with _cache_lock:
            for k in [k for k in _cache if f"/{sid}.jsonl" in k]:
                del _cache[k]
        results.append({"id": sid, "ok": moved > 0, "moved": moved, "reason": None if moved else "見つからない"})
    return {"trash": str(dest) if dest.exists() else None, "results": results}


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "ClaudeSessionViewer"

    def log_message(self, fmt, *args):
        pass

    def _host_ok(self):
        port = self.server.server_address[1]
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _auth_ok(self):
        return self._host_ok() and secrets.compare_digest(self.headers.get("X-Token", ""), TOKEN)

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        u = urlparse(self.path)
        if u.path == "/":
            html = (HERE / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
            return self._send(200, html.encode(), "text/html; charset=utf-8")
        if not self._auth_ok():
            return self._send(403, {"error": "forbidden"})
        if u.path == "/api/sessions":
            return self._send(200, list_sessions())
        if u.path == "/api/session":
            sid = parse_qs(u.query).get("id", [""])[0]
            f = find_session_file(sid) if ID_RE.match(sid) else None
            if not f:
                return self._send(404, {"error": "not found"})
            return self._send(200, {"id": sid, "messages": load_conversation(f)})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._auth_ok():
            return self._send(403, {"error": "forbidden"})
        if urlparse(self.path).path != "/api/delete":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            ids = json.loads(self.rfile.read(n)).get("ids", [])
        except (ValueError, AttributeError):
            return self._send(400, {"error": "bad request"})
        self._send(200, delete_sessions(ids))


def free_port(preferred):
    for p in [preferred] + list(range(preferred + 1, preferred + 20)):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise SystemExit("空いているポートが見つかりません")


def main():
    ap = argparse.ArgumentParser(description="Claude Code session viewer")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true", help="ブラウザを自動で開かない")
    args = ap.parse_args()
    port = free_port(args.port)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Claude Session Viewer: {url}  (Ctrl+C で終了)", flush=True)
    if not args.no_open:
        threading.Timer(0.3, webbrowser.open, [url]).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n終了しました")


if __name__ == "__main__":
    main()
