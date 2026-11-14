"""MCP tools: what any AI client (Claude, Codex, Gemini, LM Studio, ...) may do with the units.

Draft 1 rule, enforced here and not left to the model: tools change the CURRENT SOUND only.
Nothing writes a user patch slot, on any unit.
Every tool works on the ACTIVE unit unless `device` names another one ('gx10', 'katana').
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from .device import GX10
from .roland import NotConnected
from .state import State

INSTRUCTIONS = (
    "Controls BOSS guitar units live: a GX-10 processor and a KATANA Gen 3 amp. "
    "Several may be connected; gx_status lists them under 'devices' and says "
    "which is active. Tools act on the active unit; pass device='katana' or device='gx10' to pick "
    "another, or gx_select_device to change the active one (also what the app window shows). "
    "Changes go to the CURRENT SOUND only "
    "(heard instantly, not stored in any patch slot; switching patch discards them). "
    "Read the patch first (gx_read_patch) to learn fx numbers, parameter names and ranges. "
    "Use option names for list parameters (e.g. 'ON'), numbers for ranges. "
    "Use gx_undo to take back the last change. This version cannot save to a slot. "
    "The player also edits by hand (in the app window or on the unit's knobs) and may undo your "
    "changes: call gx_history before suggesting the next step to see what they did since."
)


def parse_slot(slot: str) -> int:
    """GX-10 panel label 'U66-1' -> 0-based user slot 195."""
    return GX10.parse_slot(slot)


def _safe(fn) -> dict:
    try:
        return fn()
    except (NotConnected, KeyError, ValueError, TimeoutError, FileNotFoundError, PermissionError) as e:
        return {"error": str(e)}


def register(mcp: MCPServer, s: State):
    def with_help(block: dict) -> dict:
        h = s.help.get("en", {})
        block["help"] = h.get("types", {}).get(block["type"])
        for p in block["params"]:
            p["help"] = h.get("params", {}).get(p["name"])
        return block

    @mcp.tool()
    def gx_status() -> dict:
        """Connected units ('devices', which one is 'active'), and for the active one: current patch
        (slot label and name), unsaved edits, signal chain, backup progress."""
        return s.status()

    @mcp.tool()
    def gx_select_device(device: str) -> dict:
        """Make another connected unit the active one ('gx10' or 'katana'); the app window follows."""
        return _safe(lambda: s.select_device(device))

    @mcp.tool()
    def gx_list_patches(device: str | None = None) -> dict:
        """Names of the stored patches with their panel labels (GX-10: U01-1 .. U66-3; KATANA: PANEL,
        A:CH1 .. B:CH4)."""
        return _safe(lambda: {"device": s.resolve(device), "patches": [
            {"slot": p["label"], "name": p["name"]} for p in s.need(device).patch_list()]})

    @mcp.tool()
    def gx_read_patch(fx: int | None = None, device: str | None = None) -> dict:
        """Decoded current sound: chain in signal order; each block with its parameters (name, value,
        display text, min, max, options, short help). Pass fx to get only that block."""
        def f():
            gx = s.need(device)
            with gx.lock:
                summ = gx.summary()
                ids = [fx] if fx else [c["fx"] for c in summ["chain"]]
                return {"device": gx.kind, "readonly": gx.readonly, "patch": summ["label"], "name": summ["name"],
                        "blocks": [with_help(gx.describe_fx(n)) for n in ids]}
        return _safe(f)

    @mcp.tool()
    def gx_set(fx: int, param: str, value: int | str, device: str | None = None) -> dict:
        """Change one parameter of block `fx` in the current sound, heard at once. `value` is a number
        within min..max, or an option name for list parameters. Not saved to the patch."""
        return _safe(lambda: s.need(device).set_param(fx, param, value, who="ai"))

    @mcp.tool()
    def gx_switch(fx: int, on: bool, device: str | None = None) -> dict:
        """Turn block `fx` on or off in the current sound."""
        return _safe(lambda: s.need(device).set_switch(fx, on, who="ai"))

    @mcp.tool()
    def gx_history(since: int = 0, limit: int = 50) -> dict:
        """What happened to the sound, oldest first: every change, undo, redo and patch switch, with
        who did it ("you" = the player in the app window, "unit" = a knob or switch on the unit itself,
        "ai" = an AI through these tools). Each event has a seq number; pass the last seq you saw as
        `since` to get only newer events. Changes are also kept on disk across restarts."""
        ev = s.log.since(since, limit)
        return {"events": ev, "last_seq": s.log.seq}

    @mcp.tool()
    def gx_set_chain(order: list[int], device: str | None = None) -> dict:
        """Reorder the effects of the current sound. `order` lists the fx numbers in the new signal
        order and must contain exactly the fx now on the chain (see gx_status). Only for straight
        chains (no divider/splitter); undo with gx_undo. Not saved to the patch. GX-10 only: the KATANA
        has nine fixed orders, chosen with the CHAIN parameter of its AMP block (gx_set)."""
        return _safe(lambda: s.need(device).set_chain(order, who="ai"))

    @mcp.tool()
    def gx_undo(device: str | None = None) -> dict:
        """Take back the last change to the current sound, whoever made it."""
        return _safe(lambda: s.need(device).undo(who="ai") or {"info": "nothing to undo"})

    @mcp.tool()
    def gx_redo(device: str | None = None) -> dict:
        """Re-apply the last undone change."""
        return _safe(lambda: s.need(device).redo(who="ai") or {"info": "nothing to redo"})

    @mcp.tool()
    def gx_select_patch(slot: str, device: str | None = None) -> dict:
        """Switch the unit to a stored patch, e.g. 'U66-1' on the GX-10. Unsaved changes to the current
        sound are lost. KATANA slots: PANEL, A:CH1 .. A:CH4, B:CH1 .. B:CH4."""
        def f():
            d = s.need(device)
            d.select_patch(d.parse_slot(slot), who="ai")
            return d.summary()
        return _safe(f)

    @mcp.tool()
    def gx_backup_all() -> dict:
        """Back up every stored patch of the active unit (GX-10: 198, KATANA: 9) into the default backup
        folder. Returns at once; gx_status shows progress and the file path."""
        return _safe(s.start_backup)
