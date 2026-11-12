"""MCP tools: what any AI client (Claude, Codex, Gemini, LM Studio, ...) may do with the GX-10.

Draft 1 rule, enforced here and not left to the model: tools change the CURRENT SOUND only.
Nothing writes a user patch slot.
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from .device import VISIBLE_USER, slot_label
from .roland import NotConnected
from .state import State

INSTRUCTIONS = (
    "Controls a BOSS GX-10 guitar processor live. Changes go to the CURRENT SOUND only "
    "(heard instantly, not stored in any patch slot; switching patch discards them). "
    "Read the patch first (gx_read_patch) to learn fx numbers, parameter names and ranges. "
    "Use option names for list parameters (e.g. 'ON'), numbers for ranges. "
    "Use gx_undo to take back the last change. This version cannot save to a slot. "
    "The player also edits by hand (in the app window or on the unit's knobs) and may undo your "
    "changes: call gx_history before suggesting the next step to see what they did since."
)


def parse_slot(slot: str) -> int:
    """'U66-1' -> 0-based user slot 195."""
    bank, num = slot.upper().strip().lstrip("U").split("-")
    n = (int(bank) - 1) * 3 + int(num) - 1
    if not 0 <= n < VISIBLE_USER or not 1 <= int(num) <= 3:
        raise ValueError("slot must be U01-1 .. U66-3")
    return n


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
        """Connection state, current patch (slot label and name), unsaved edits, signal chain, backup progress."""
        return s.status()

    @mcp.tool()
    def gx_list_patches() -> dict:
        """Names of the 198 user patches, as panel labels U01-1 .. U66-3."""
        return _safe(lambda: {"patches": [{"slot": slot_label(i), "name": n}
                                          for i, n in enumerate(s.need().names)]})

    @mcp.tool()
    def gx_read_patch(fx: int | None = None) -> dict:
        """Decoded current sound: chain in signal order; each block with its parameters (name, value,
        display text, min, max, options, short help). Pass fx to get only that block."""
        def f():
            gx = s.need()
            with gx.lock:
                summ = gx.summary()
                ids = [fx] if fx else [c["fx"] for c in summ["chain"]]
                return {"patch": summ["label"], "name": summ["name"],
                        "blocks": [with_help(gx.describe_fx(n)) for n in ids]}
        return _safe(f)

    @mcp.tool()
    def gx_set(fx: int, param: str, value: int | str) -> dict:
        """Change one parameter of block `fx` in the current sound, heard at once. `value` is a number
        within min..max, or an option name for list parameters. Not saved to the patch."""
        return _safe(lambda: s.need().set_param(fx, param, value, who="ai"))

    @mcp.tool()
    def gx_switch(fx: int, on: bool) -> dict:
        """Turn block `fx` on or off in the current sound."""
        return _safe(lambda: s.need().set_switch(fx, on, who="ai"))

    @mcp.tool()
    def gx_history(since: int = 0, limit: int = 50) -> dict:
        """What happened to the sound, oldest first: every change, undo, redo and patch switch, with
        who did it ("you" = the player in the app window, "unit" = a knob or switch on the GX-10 itself,
        "ai" = an AI through these tools). Each event has a seq number; pass the last seq you saw as
        `since` to get only newer events. Changes are also kept on disk across restarts."""
        ev = s.log.since(since, limit)
        return {"events": ev, "last_seq": s.log.seq}

    @mcp.tool()
    def gx_set_chain(order: list[int]) -> dict:
        """Reorder the effects of the current sound. `order` lists the fx numbers in the new signal
        order and must contain exactly the fx now on the chain (see gx_status). Only for straight
        chains (no divider/splitter); undo with gx_undo. Not saved to the patch."""
        return _safe(lambda: s.need().set_chain(order, who="ai"))

    @mcp.tool()
    def gx_undo() -> dict:
        """Take back the last change to the current sound, whoever made it."""
        return _safe(lambda: s.need().undo(who="ai") or {"info": "nothing to undo"})

    @mcp.tool()
    def gx_redo() -> dict:
        """Re-apply the last undone change."""
        return _safe(lambda: s.need().redo(who="ai") or {"info": "nothing to redo"})

    @mcp.tool()
    def gx_select_patch(slot: str) -> dict:
        """Switch the GX-10 to a user patch, e.g. 'U66-1'. Unsaved changes to the current sound are lost."""
        def f():
            s.need().select_patch(parse_slot(slot), who="ai")
            return s.need().summary()
        return _safe(f)

    @mcp.tool()
    def gx_backup_all() -> dict:
        """Back up all 198 user patches into the default backup folder. Returns at once;
        gx_status shows progress and the file path."""
        return _safe(s.start_backup)
