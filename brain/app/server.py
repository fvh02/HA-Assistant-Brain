"""HA-Assistant-Brain: the long-term memory of the assistant and the add-ons, kept as an Obsidian vault.

Add-ons and the assistant send infos (POST /api/remember). They wait in the inbox with the time they were
received and the time they happened, and are sorted into the vault in batches by Claude. Other add-ons read
the memory through this add-on (search, single notes, a ready-made context text, the owner's profile).
Every night duplicates are merged, long notes condensed and old diary days summarised.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from aiohttp import web

import claude
from index import Index
from memory import Inbox, Memory, Settings, parse_info
from vault import Vault, fmt_ts, now

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("brain")

DATA = Path(os.environ.get("BRAIN_DATA", "/data"))
STATIC = Path(__file__).parent / "static"
PORT = int(os.environ.get("BRAIN_PORT", "8096"))
INGRESS_IP = "172.30.32.2"


def load_options() -> dict:
    try:
        return json.loads((DATA / "options.json").read_text())
    except (OSError, ValueError):
        return {}


OPT = load_options()
VAULT_DIR = Path(str(OPT.get("vault_dir") or "/share/brain-vault"))
API_KEY = str(OPT.get("api_key") or "").strip()
BATCH_SECONDS = float(os.environ.get("BRAIN_BATCH_SECONDS") or OPT.get("batch_seconds") or 20)
CONSOLIDATE_HOUR = int(OPT.get("consolidate_hour") if OPT.get("consolidate_hour") is not None else 3)
if OPT.get("claude_oauth_token"):
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = str(OPT["claude_oauth_token"]).strip()
SETTINGS = Settings(
    model=str(OPT.get("claude_model") or "").strip(),
    bridge_url=str(OPT.get("claude_bridge_url") or "").strip(),
    batch_size=int(OPT.get("batch_size") or 10),
    max_facts=int(OPT.get("max_facts") or 30),
    events_days=int(OPT.get("events_compress_days") or 60),
)

state = {"busy": "", "error": "", "error_at": None, "last_ingest": None, "last_report": None,
         "last_consolidate": None, "consolidate_report": None, "started": fmt_ts(now())}
wake = asyncio.Event()
routes = web.RouteTableDef()
memory: Memory | None = None


# -- access ---------------------------------------------------------------------------------------------
@web.middleware
async def guard(request: web.Request, handler):
    """With an API key set, other add-ons must send it; the dashboard (ingress) is always allowed."""
    if API_KEY and request.path.startswith("/api/") and request.remote != INGRESS_IP:
        given = request.headers.get("X-Brain-Key") or request.query.get("key") or ""
        if given != API_KEY:
            return web.json_response({"error": "Falscher oder fehlender X-Brain-Key"}, status=403)
    return await handler(request)


async def body(request: web.Request) -> dict:
    try:
        data = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise web.HTTPBadRequest(text=json.dumps({"error": "JSON erwartet"}), content_type="application/json")
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text=json.dumps({"error": "JSON-Objekt erwartet"}), content_type="application/json")
    return data


# -- background work ------------------------------------------------------------------------------------
async def worker() -> None:
    """Sort the inbox in: wait a few seconds after the first info so infos that belong together form one batch."""
    while True:
        try:
            await asyncio.wait_for(wake.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass
        wake.clear()
        if not memory.inbox.counts().get("offen"):
            continue
        await asyncio.sleep(BATCH_SECONDS)
        while memory.inbox.counts().get("offen"):
            state["busy"] = "einsortieren"
            try:
                report = await asyncio.to_thread(memory.ingest)
            except Exception as err:  # noqa: BLE001
                log.warning("Einsortieren fehlgeschlagen: %s", err)
                state["error"], state["error_at"] = str(err), fmt_ts(now())
                state["busy"] = ""
                await asyncio.sleep(120)  # Claude unreachable: try again later, the infos stay in the inbox
                break
            state["busy"] = ""
            if report is None:
                break
            state["error"] = ""
            state["last_ingest"], state["last_report"] = fmt_ts(now()), report


async def nightly() -> None:
    last_day = None
    while True:
        await asyncio.sleep(300)
        t = datetime.now()
        if t.hour == CONSOLIDATE_HOUR and last_day != t.date():
            last_day = t.date()
            await run_consolidate()


async def run_consolidate() -> dict:
    state["busy"] = "aufräumen"
    try:
        report = await asyncio.to_thread(memory.consolidate)
        state["last_consolidate"], state["consolidate_report"] = fmt_ts(now()), report
        log.info("Aufräumen fertig: %s", report)
        return report
    finally:
        state["busy"] = ""


# -- API --------------------------------------------------------------------------------------------------
@routes.get("/")
async def index_page(_: web.Request) -> web.Response:
    return web.FileResponse(STATIC / "index.html")


@routes.post("/api/remember")
async def api_remember(request: web.Request) -> web.Response:
    """One info {"text", "source", "occurred_at"?, "subject"?} or several as {"infos": [...]}."""
    data = await body(request)
    items = data["infos"] if isinstance(data.get("infos"), list) else [data]
    stored = []
    for item in items[:100]:
        if not isinstance(item, dict):
            continue
        text, source, occurred, subject = parse_info(item)
        if not text:
            continue
        stored.append(memory.inbox.add(text, source or request.headers.get("X-Brain-Source", ""), occurred, subject))
    if not stored:
        return web.json_response({"error": "Kein Text"}, status=400)
    wake.set()
    return web.json_response({"ok": True, "gespeichert": stored})


@routes.get("/api/search")
async def api_search(request: web.Request) -> web.Response:
    q = request.query.get("q", "")
    limit = min(30, int(request.query.get("limit") or 8))
    return web.json_response(memory.index.search(q, limit=limit, kind=request.query.get("kind") or None))


@routes.get("/api/note")
async def api_note(request: web.Request) -> web.Response:
    note = memory.note(request.query.get("title", ""), request.query.get("path", ""))
    return web.json_response(note) if note else web.json_response({"error": "Nicht gefunden"}, status=404)


@routes.get("/api/context")
async def api_context(request: web.Request) -> web.Response:
    """What the memory knows about a topic, as text to put into a prompt."""
    q = request.query.get("q", "")
    max_chars = max(500, min(20000, int(request.query.get("max_chars") or 4000)))
    profile = request.query.get("profile", "1") not in ("0", "false", "nein")
    text = await asyncio.to_thread(memory.context, q, max_chars, profile)
    return web.json_response({"text": text, "q": q})


@routes.get("/api/profile")
async def api_profile(_: web.Request) -> web.Response:
    return web.json_response({"text": memory.vault.read_profile()})


@routes.get("/api/events")
async def api_events(request: web.Request) -> web.Response:
    days = max(1, min(60, int(request.query.get("days") or 7)))
    return web.json_response(memory.events(days))


@routes.get("/api/inbox")
async def api_inbox(request: web.Request) -> web.Response:
    return web.json_response(memory.inbox.recent(min(200, int(request.query.get("limit") or 50))))


@routes.post("/api/inbox/retry")
async def api_retry(_: web.Request) -> web.Response:
    n = memory.inbox.retry()
    wake.set()
    return web.json_response({"ok": True, "erneut": n})


@routes.get("/api/status")
async def api_status(_: web.Request) -> web.Response:
    return web.json_response({
        **state, "inbox": memory.inbox.counts(), "vault": str(VAULT_DIR), "stats": memory.index.stats(),
        "claude": claude.source(SETTINGS.bridge_url), "consolidate_hour": CONSOLIDATE_HOUR,
    })


@routes.get("/api/notes")
async def api_notes(request: web.Request) -> web.Response:
    return web.json_response(memory.index.titles(min(2000, int(request.query.get("limit") or 500))))


@routes.post("/api/consolidate")
async def api_consolidate(_: web.Request) -> web.Response:
    if state["busy"]:
        return web.json_response({"error": f"Gerade beschäftigt ({state['busy']})"}, status=409)
    asyncio.create_task(run_consolidate())
    return web.json_response({"ok": True, "gestartet": True})


@routes.post("/api/rebuild")
async def api_rebuild(_: web.Request) -> web.Response:
    n = await asyncio.to_thread(memory.index.rebuild)
    return web.json_response({"ok": True, "notizen": n})


# -- start ------------------------------------------------------------------------------------------------
async def on_startup(app: web.Application) -> None:
    global memory
    vault = Vault(VAULT_DIR)
    index = Index(DATA / "index.db", vault)
    started = time.time()
    n = await asyncio.to_thread(index.rebuild)
    memory = Memory(vault, index, Inbox(DATA / "inbox.db"), claude.ask, SETTINGS)
    log.info("Gedächtnis bereit: %d Notizen in %s (Index in %.1f s), Claude über %s, Aufräumen um %d Uhr",
             n, VAULT_DIR, time.time() - started, claude.source(SETTINGS.bridge_url), CONSOLIDATE_HOUR)
    app["tasks"] = [asyncio.create_task(worker()), asyncio.create_task(nightly())]
    wake.set()


async def on_cleanup(app: web.Application) -> None:
    for task in app["tasks"]:
        task.cancel()


def main() -> None:
    app = web.Application(middlewares=[guard], client_max_size=4 * 1024 * 1024)
    app.add_routes(routes)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    web.run_app(app, host=None, port=PORT, print=None, access_log=None)


if __name__ == "__main__":
    main()
