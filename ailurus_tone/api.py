"""REST endpoints for the app window. Thin: every route delegates to State / GX10."""
from __future__ import annotations

from pathlib import Path

import anyio
from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from .backup import load_config, save_config
from .device import slot_label
from .roland import NotConnected
from .state import State

WEB = Path(__file__).parent / "web"
USER_ERRORS = (NotConnected, KeyError, ValueError, TimeoutError, FileNotFoundError, PermissionError)


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


def set_backup_dir(path: str) -> dict:
    p = Path(path).expanduser()
    if not p.is_dir():
        raise FileNotFoundError(f"Folder does not exist: {p}")
    cfg = load_config()
    cfg["backup_dir"] = str(p)
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
        return await _run(s.status)

    @route("/api/help", methods=["GET"])
    async def help_texts(_: Request) -> Response:
        return JSONResponse(s.help)

    @route("/api/patches", methods=["GET"])
    async def patches(_: Request) -> Response:
        return await _run(lambda: {"patches": [{"slot": i, "label": slot_label(i), "name": n}
                                               for i, n in enumerate(s.need().names)]})

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
        return await _run(lambda: set_backup_dir(b["backup_dir"]))
