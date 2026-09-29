"""Local web UI for the GEO tracker.

Start it with:  python -m geo.webapp   (opens http://127.0.0.1:8000)

It's a thin front door over the existing CLI: the batch and the login flow each
run as a subprocess so the browser automation stays isolated from the web
server. The page polls /api/progress for the live log and progress bar.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file

from . import config

_WEB_DIR = Path(__file__).parent / "web"
app = Flask(__name__, static_folder=str(_WEB_DIR / "assets"), static_url_path="/assets")
PY = sys.executable
# A completed run is a friendly "✓ … run N of N" line (see runner.py).
_RESULT_RE = re.compile(r"^✓.*run \d+ of \d+")
# Raw stack-trace / driver-internal lines we hide from the human-facing log.
_NOISE_RE = re.compile(
    r"^\s*(at |File \"|Traceback \(|self\.|await |raise |\^+\s*$)"
    r"|coreBundle\.js|node:events|playwright/[_a-z]|/site-packages/|Node\.js v"
    r"|greenlet|asyncio|_sync\(",
    re.IGNORECASE,
)

# Single active subprocess at a time (one run or one login).
_MAX_RESTARTS = 40   # supervisor cap: how many times to auto-resume a crashed run
_STALL_SECONDS = 480  # kill a run that prints nothing for this long (it's hung)

STATE = {
    "proc": None,
    "kind": None,          # "run" | "login"
    "lines": deque(maxlen=2000),
    "done": 0,
    "total": 0,
    "running": False,
    "cmd": None,
    "stdin": False,
    "stopped": False,      # set by /api/stop so the supervisor won't resume
    "restarts": 0,
    "last_line_at": 0.0,   # for the stall watchdog
}


def _kill_group(proc: subprocess.Popen) -> None:
    """Kill the subprocess AND its browser children (own process group)."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except Exception:
            pass


def _spawn() -> None:
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(
        STATE["cmd"], cwd=str(config.ROOT), env=env, text=True, bufsize=1,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE if STATE["stdin"] else None,
        start_new_session=True,  # own process group so we can kill browser children
    )
    STATE["proc"] = proc
    STATE["running"] = True
    STATE["last_line_at"] = time.time()
    threading.Thread(target=_reader, args=(proc,), daemon=True).start()


def _watchdog() -> None:
    """Kill a run that has gone silent for too long; the supervisor then resumes it."""
    while True:
        time.sleep(30)
        proc = STATE["proc"]
        if (STATE["running"] and STATE["kind"] == "run" and proc
                and time.time() - STATE["last_line_at"] > _STALL_SECONDS):
            STATE["lines"].append(
                "↻  The run went quiet for a while (likely a stuck browser) — "
                "restarting it automatically…")
            _kill_group(proc)  # process exits -> _reader's supervisor auto-resumes


def _reader(proc: subprocess.Popen) -> None:
    for line in proc.stdout:  # type: ignore[union-attr]
        line = line.rstrip("\n")
        STATE["last_line_at"] = time.time()
        if line and _NOISE_RE.search(line):
            continue  # hide developer stack traces from the human-facing log
        STATE["lines"].append(line)
        if _RESULT_RE.search(line):
            STATE["done"] += 1
    rc = proc.poll()
    STATE["running"] = False
    STATE["proc"] = None
    # Auto-resume a run that died unexpectedly (e.g. a browser-driver crash that
    # takes down the whole process). Resume is safe — it skips completed rows.
    if (STATE["kind"] == "run" and not STATE["stopped"]
            and rc not in (0, None) and STATE["restarts"] < _MAX_RESTARTS):
        STATE["restarts"] += 1
        STATE["lines"].append(
            f"↻  The run stopped unexpectedly — automatically picking up where it "
            f"left off (attempt {STATE['restarts']} of {_MAX_RESTARTS})…")
        time.sleep(3)
        _spawn()
    else:
        STATE["kind"] = None


def _start(cmd: list[str], kind: str, total: int = 0, stdin: bool = False) -> bool:
    if STATE["running"]:
        return False
    STATE["lines"].clear()
    STATE["done"] = 0
    STATE["total"] = total
    STATE["kind"] = kind
    STATE["cmd"] = cmd
    STATE["stdin"] = stdin
    STATE["stopped"] = False
    STATE["restarts"] = 0
    _spawn()
    return True


def _output_files() -> list[str]:
    if not config.OUTPUT_DIR.exists():
        return []
    files = sorted(config.OUTPUT_DIR.glob("*.xlsx"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return [f.name for f in files]


@app.get("/")
def index():
    return (_WEB_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/status")
def status():
    prompts = config.load_prompts()
    profile = config.PROFILE_DIR
    logged_in = profile.exists() and any(profile.iterdir())
    return jsonify({
        "provider": config.ANALYSIS_PROVIDER,
        "key_set": bool(config.analysis_key()),
        "profile_found": logged_in,
        "prompt_count": len(prompts),
        "platforms": config.PLATFORMS,
        "runs_per_prompt": config.RUNS_PER_PROMPT,
        "bracketed": [p for p in prompts if "[" in p and "]" in p],
        "outputs": _output_files(),
        "running": STATE["running"],
        "kind": STATE["kind"],
    })


@app.get("/api/prompts")
def get_prompts():
    text = config.PROMPTS_FILE.read_text(encoding="utf-8") if config.PROMPTS_FILE.exists() else ""
    return jsonify({"text": text})


@app.post("/api/prompts")
def save_prompts():
    config.PROMPTS_FILE.write_text(request.json.get("text", ""), encoding="utf-8")
    return jsonify({"ok": True})


@app.get("/api/progress")
def progress():
    return jsonify({
        "running": STATE["running"],
        "kind": STATE["kind"],
        "done": STATE["done"],
        "total": STATE["total"],
        "lines": list(STATE["lines"]),
        "outputs": _output_files(),
    })


_RUN_CACHE: dict[str, tuple[float, dict]] = {}  # name -> (mtime, summary)


def _run_summary(path: Path) -> dict:
    """Leaderboard + top sources for one workbook, cached until it changes."""
    from .excel_io import Tracker
    from .template import LOG_HEADERS
    mtime = path.stat().st_mtime
    cached = _RUN_CACHE.get(path.name)
    if cached and cached[0] == mtime:
        return cached[1]
    stamp = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})", path.name)
    info = {"file": path.name,
            "date": f"{stamp[1]} {stamp[2]}:{stamp[3]}" if stamp else "",
            "leaderboard": [], "domains": [], "prompts": [], "done": 0, "total": 0}
    try:
        tracker = Tracker(path)
        headers = [c.value for c in tracker.log[1]][:len(LOG_HEADERS)]
        if headers != LOG_HEADERS:
            info["legacy"] = True  # older brand-tracking workbook: download only
        else:
            tracker._reindex()
            info.update(
                leaderboard=tracker.leaderboard(), domains=tracker.domains()[:15],
                prompts=[p for _, p in tracker.prompts()],
                done=sum(1 for _ in tracker._completed_rows()), total=len(tracker._index))
    except Exception as err:  # mid-save; retry on the next poll
        info["error"] = str(err)
        return info
    _RUN_CACHE[path.name] = (mtime, info)
    return info


@app.get("/api/runs")
def runs():
    """Every run (output workbook) newest first, each with its leaderboard."""
    return jsonify({"platforms": config.PLATFORMS,
                    "runs": [_run_summary(config.OUTPUT_DIR / n) for n in _output_files()]})


@app.post("/api/run")
def run():
    data = request.json or {}
    platforms = data.get("platforms") or config.PLATFORMS
    runs = int(data.get("runs") or config.RUNS_PER_PROMPT)
    limit = data.get("limit")
    headful = bool(data.get("headful"))
    n_prompts = limit if limit else len(config.load_prompts())
    total = int(n_prompts) * len(platforms) * runs

    cmd = [PY, "-m", "geo.cli", "run", "--runs", str(runs), "--platforms", *platforms]
    if limit:
        cmd += ["--limit", str(int(limit))]
    if headful:
        cmd += ["--headful"]
    if not _start(cmd, "run", total=total):
        return jsonify({"ok": False, "error": "A job is already running."}), 409
    return jsonify({"ok": True})


@app.post("/api/login")
def login():
    if not _start([PY, "-m", "geo.cli", "login"], "login", stdin=True):
        return jsonify({"ok": False, "error": "A job is already running."}), 409
    return jsonify({"ok": True})


@app.post("/api/login/done")
def login_done():
    proc = STATE["proc"]
    if proc and STATE["kind"] == "login" and proc.stdin:
        try:
            proc.stdin.write("\n")
            proc.stdin.flush()
        except Exception:
            pass
    return jsonify({"ok": True})


@app.post("/api/stop")
def stop():
    STATE["stopped"] = True  # prevent the supervisor from auto-resuming
    proc = STATE["proc"]
    if proc:
        _kill_group(proc)  # kill the run and its browser children together
    return jsonify({"ok": True})


@app.get("/api/download")
def download():
    name = request.args.get("name", "")
    path = config.OUTPUT_DIR / name
    if path.name != name or not path.exists():  # prevent path traversal
        return "Not found", 404
    return send_file(path, as_attachment=True)


def main() -> None:
    url = "http://127.0.0.1:8000"
    print(f"GEO tracker UI running at {url}")
    threading.Thread(target=_watchdog, daemon=True).start()
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=8000, threaded=True)


if __name__ == "__main__":
    main()
