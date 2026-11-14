"""REST endpoints for the app window. Thin: every route delegates to State and the active unit."""
from __future__ import annotations

from pathlib import Path

import anyio
from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from . import updater
from .about import about
from .backup import load_config, save_config
from .locate import BTSError
from .roland import NotConnected
from .state import State

WEB = Path(__file__).parent / "web"
USER_ERRORS = (NotConnected, KeyError, ValueError, TimeoutError, FileNotFoundError, PermissionError, BTSError,
               RuntimeError)


async def _body(req: Request) -> dict:
    try:
        return await req.json()
    except ValueError:
        return {}


async def _run(fn) -> Response:
    """Run blocking MIDI work off the event loop; user errors become 400 with a readable message."""
    def call():
        try:
            r = fn()
            return JSONResponse(r if r is not None else {"ok": True})
        except USER_ERRORS as e:
            return JSONResponse({"error": str(e)}, status_code=400)
    return await anyio.to_thread.run_sync(call)


def set_config(b: dict) -> dict:
    cfg = load_config()
    if "backup_dir" in b:
        p = Path(b["backup_dir"]).expanduser()
        if not p.is_dir():
            raise FileNotFoundError(f"Folder does not exist: {p}")
        cfg["backup_dir"] = str(p)
    if "ai_can_save" in b:
        cfg["ai_can_save"] = bool(b["ai_can_save"])
    save_config(cfg)
    return cfg


def register(mcp: MCPServer, s: State):
    route = mcp.custom_route

    @route("/", methods=["GET"])
    async def index(_: Request) -> Response:
        return FileResponse(WEB / "index.html")

    @route("/static/{name}", methods=["GET"])
    async def static(req: Request) -> Response:
        f = (WEB / req.path_params["name"]).resolve()
        if f.parent != WEB.resolve() or not f.is_file():
            return Response(status_code=404)
        return FileResponse(f)

    @route("/api/state", methods=["GET"])
    async def state(_: Request) -> Response:
        return await _run(lambda: s.status() | {"update": updater.notice()})

    @route("/api/help", methods=["GET"])
    async def help_texts(_: Request) -> Response:
        return JSONResponse(s.help)

    @route("/api/patches", methods=["GET"])
    async def patches(_: Request) -> Response:
        return await _run(lambda: {"device": s.active, "patches": s.need().patch_list()})

    @route("/api/device", methods=["POST"])
    async def device(req: Request) -> Response:
        """Choose which connected unit the window edits."""
        b = await _body(req)
        return await _run(lambda: s.select_device(str(b["id"])))

    @route("/api/fx/{n:int}", methods=["GET"])
    async def fx(req: Request) -> Response:
        return await _run(lambda: s.need().describe_fx(req.path_params["n"]))

    @route("/api/param", methods=["POST"])
    async def param(req: Request) -> Response:
        b = await _body(req)
        return await _run(lambda: s.need().set_param(int(b["fx"]), b["param"], b["value"],
                                                     record=b.get("record", True)))

    @route("/api/record", methods=["POST"])
    async def record(req: Request) -> Response:
        """Knob released: ONE history entry for the whole drag (old -> final)."""
        b = await _body(req)
        return await _run(lambda: s.need().record_change(int(b["fx"]), b["param"], int(b["old"]), int(b["new"])))

    @route("/api/switch", methods=["POST"])
    async def switch(req: Request) -> Response:
        b = await _body(req)
        return await _run(lambda: s.need().set_switch(int(b["fx"]), bool(b["on"])))

    @route("/api/chain", methods=["POST"])
    async def chain(req: Request) -> Response:
        b = await _body(req)
        return await _run(lambda: s.need().set_chain(b["order"]))

    @route("/api/undo", methods=["POST"])
    async def undo(_: Request) -> Response:
        return await _run(lambda: s.need().undo())

    @route("/api/redo", methods=["POST"])
    async def redo(_: Request) -> Response:
        return await _run(lambda: s.need().redo())

    @route("/api/select", methods=["POST"])
    async def select(req: Request) -> Response:
        b = await _body(req)
        return await _run(lambda: s.need().select_patch(int(b["slot"])))

    @route("/api/backup", methods=["POST"])
    async def backup(req: Request) -> Response:
        b = await _body(req)
        return await _run(lambda: s.start_backup(b.get("folder")))

    @route("/api/config", methods=["POST"])
    async def config(req: Request) -> Response:
        b = await _body(req)
        return await _run(lambda: set_config(b))

    @route("/api/save", methods=["POST"])
    async def save(req: Request) -> Response:
        b = await _body(req)
        return await _run(lambda: s.save_patch(int(b["slot"]), b.get("name")))

    @route("/api/overwritten", methods=["GET"])
    async def overwritten(_: Request) -> Response:
        return await _run(lambda: {"items": s.overwritten()})

    @route("/api/putback", methods=["POST"])
    async def putback(req: Request) -> Response:
        b = await _body(req)
        return await _run(lambda: s.put_back(str(b["file"])))

    @route("/api/ir", methods=["GET"])
    async def ir_slots(_: Request) -> Response:
        return await _run(s.ir_slots)

    @route("/api/ir/load", methods=["POST"])
    async def ir_load(req: Request) -> Response:
        """A .wav as the raw body; ?slot=0..15&file=<file name>&name=<IR name>&replace=1."""
        q = req.query_params
        data = await req.body()
        if not data or len(data) > 50_000_000:
            return JSONResponse({"error": "Choose a .wav file (an impulse response)."}, status_code=400)
        return await _run(lambda: s.ir_load(int(q.get("slot", -1)), data, q.get("file", ""), q.get("name") or None,
                                            replace=q.get("replace") == "1"))

    @route("/api/ir/rename", methods=["POST"])
    async def ir_rename(req: Request) -> Response:
        b = await _body(req)
        return await _run(lambda: s.ir_rename(int(b["slot"]), str(b["name"])))

    @route("/api/about", methods=["GET"])
    async def about_app(_: Request) -> Response:
        return JSONResponse(about(getattr(s, "port", 8710), updater.available())
                            | {"can_quit": getattr(s, "quit", None) is not None})

    @route("/api/quit", methods=["POST"])
    async def quit_app(_: Request) -> Response:
        """Linux (UI in the browser): there is no window to close, so the page offers Quit."""
        q = getattr(s, "quit", None)
        if not q:
            return JSONResponse({"error": "Close the app window to quit."}, status_code=400)
        q()
        return JSONResponse({"ok": True})

    @route("/api/update/check", methods=["POST"])
    async def check_update(_: Request) -> Response:
        """Settings → About → Check for updates: Sparkle/WinSparkle show their own window; on
        Linux the answer comes back here ({latest, url, newer})."""
        if not updater.available():
            return JSONResponse({"error": "Updates are checked by the installed app only."}, status_code=400)

        def check():
            try:
                return updater.check_now()
            except (OSError, ValueError) as e:
                raise TimeoutError(f"Could not reach GitHub to check for updates: {e}") from e
        return await _run(check)

    @route("/api/bts/installer", methods=["POST"])
    async def bts_installer(req: Request) -> Response:
        """Linux: the BTS Windows installer as the raw body; ?id=<unit>&name=<file name>."""
        q = req.query_params
        data = await req.body()
        if not data or len(data) > 300_000_000:
            return JSONResponse({"error": "Choose the BOSS TONE STUDIO installer (.exe or .zip)."}, status_code=400)
        return await _run(lambda: s.install_bts(q.get("id", ""), q.get("name", ""), data))

    @route("/api/bts/download", methods=["POST"])
    async def bts_download(req: Request) -> Response:
        """Linux: {id, agreed}: download Roland's BTS for Windows and unpack it (agreed = the user
        accepted Roland's software licence agreement in Settings)."""
        b = await _body(req)
        return await _run(lambda: s.install_bts(str(b.get("id", "")), agreed=b.get("agreed") is True))

    @route("/api/bts", methods=["POST"])
    async def bts(req: Request) -> Response:
        """Where a unit's BOSS TONE STUDIO is: {id, path}; an empty path = look in the usual places."""
        b = await _body(req)
        return await _run(lambda: s.set_bts_path(str(b["id"]), b.get("path")))
