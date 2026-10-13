#!/usr/bin/env python3
"""Write docs/SETTINGS.md from mods/core/nfsmw/mod.toml, so the settings list can never drift from the code.

    python3 tools/setup_kit/settings_doc.py            # rewrite docs/SETTINGS.md
    python3 tools/setup_kit/settings_doc.py --check    # exit 1 if docs/SETTINGS.md is out of date"""
import re, sys, tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOD = ROOT / "mods/core/nfsmw/mod.toml"
OUT = ROOT / "docs/SETTINGS.md"
# The menu's pages and sections, in its order (kit/mods/settings_menu.cpp kSections; 3.0: pages named by what a player
# wants to change, Graphics Advanced by effect). Group "hidden" is never listed.
PAGES = [("Graphics", [("graphics", "Picture"), ("graphics.lighting", "Advanced: Lighting and shadows"),
                       ("graphics.surfaces", "Advanced: Textures, surfaces and reflections"),
                       ("graphics.camera", "Advanced: Camera and colour effects"),
                       ("graphics.weather", "Advanced: Sky, weather and rain"),
                       ("graphics.particles", "Advanced: Smoke and sparks"),
                       ("graphics.mirror", "Advanced: Rear-view mirror"),
                       ("graphics.engine", "Advanced: Detail limits and engine")]),
         ("Display", [("display", "Screen"), ("display.quality", "Sharpness and upscaling"), ("display.hdr", "HDR")]),
         ("Audio", [("audio", "Audio")]),
         ("Interface", [("ui", "HUD and minimap"), ("ui.menus", "Menus, movies and subtitles")]),
         ("Gameplay", [("game", "Career and rewards"), ("game.police", "Police and heat"),
                       ("game.unlocks", "Unlocks and shortcuts"), ("game.fixes", "Fixes (leave on)")]),
         ("Mods", [("mods", "Your mods"), ("diagnostics", "Diagnostics"), ("", "Other")]),
         ("Controls", [("controls", "Controller"), ("feedback", "Controller rumble"), ("controls.keys", "Keyboard keys")])]
# The settings that change speed the most, first (the guide's "Start here").
START = ["mac_preset", "upscale", "antialiasing", "hdr_output", "quality"]
APPLY = {"live": "at once", "restart": "after a restart", "renderer": "after Apply", "next_screen": "at the next screen or load",
         "new_career": "for new careers", "game_video": "with the game's video settings"}


def value_text(spec):
    d = spec.get("default")
    if spec.get("type") == "bool":
        return "on" if d else "off"
    if "choices" in spec:
        choices = spec["choices"].split("|")
        lo = spec.get("min", 0)
        i = d - lo
        if 0 <= i < len(choices) and choices[i]:
            return choices[i]
    unit = spec.get("unit", "")
    return f"{unit}{d}" if unit == "$" else f"{d}"


def anchor(title):
    return re.sub(r"[^a-z0-9 -]", "", title.lower()).replace(" ", "-")


def choices_text(spec):
    if spec.get("type") == "bool":
        return "Off / On"
    if spec.get("choices"):
        return " / ".join(c for c in spec["choices"].split("|") if c)
    lo, hi, step = spec.get("min"), spec.get("max"), spec.get("step")
    return f"{lo} to {hi}" + (f", steps of {step}" if step else "") if lo is not None and hi is not None else ""


def cell(text):
    return re.sub(r"\s+", " ", text).replace("|", "/")


def render():
    settings = tomllib.loads(MOD.read_text())["settings"]
    groups = {}
    for key, spec in settings.items():
        groups.setdefault(spec.get("group", ""), []).append((key, spec))
    out = ["# Settings", "",
           "Every setting of the native app: what it does, what you see when you change it, what it costs and a tip "
           "for slower Macs. Written from `mods/core/nfsmw/mod.toml` by `tools/setup_kit/settings_doc.py` (do not edit "
           "by hand), so it always matches the game.", "",
           "Open the settings menu with **F10** (Fn+F10 on a laptop), the controller's touchpad button, or Back+Start. "
           "The menu has the same pages and sections as this guide, and shows each setting's text at the bottom. "
           "Costs are the extra time each frame takes on the graphics chip at 4K (lower is faster; 16.7 ms is one "
           "frame at 60 fps). The same keys can be set in `NativeOptions.ini` in your profile folder (see the "
           "companion guide).", "",
           "**Pages:** " + " · ".join(f"[{p}](#{anchor(p)})" for p, sections in PAGES
                                     if any(groups.get(g) for g, _ in sections)), "",
           "## Start here: speed and picture", "",
           "If the game does not keep a smooth 60 fps, change these first, in this order:", ""]
    for key in START:
        if key in settings and settings[key].get("group") != "hidden":
            spec = settings[key]
            out.append(f"- **{cell(spec.get('label', key))}** (`{key}`, {value_text(spec)} by default): "
                       f"{cell(spec.get('help', ''))}")
    out.append("")
    hidden = groups.pop("hidden", [])
    for page, sections in PAGES:
        if not any(groups.get(gid) for gid, _ in sections):
            continue
        out += [f"## {page}", ""]
        for gid, title in sections:
            rows = groups.pop(gid, [])
            if not rows:
                continue
            out += [f"### {title}", "", "| Setting | Choices | Default | Takes effect | What it does |",
                    "|---|---|---|---|---|"]
            for key, spec in rows:
                out.append(f"| **{cell(spec.get('label', key))}** `{key}` | {cell(choices_text(spec))} | "
                           f"{value_text(spec)} | {APPLY.get(spec.get('apply', ''), spec.get('apply', ''))} | "
                           f"{cell(spec.get('help', ''))} |")
            out.append("")
    if hidden:
        out += ["## Not in the menu", "",
                "Kept so that older profiles load cleanly, fixed by the port, or tuning most players never need; the "
                "menu does not list them and your saved value is kept.", "",
                "| Key | Value | Why |", "|---|---|---|"]
        for key, spec in hidden:
            out.append(f"| `{key}` | {value_text(spec)} | {cell(spec.get('help', ''))} |")
        out.append("")
    assert not groups, f"settings in unknown groups: {list(groups)}"
    return "\n".join(out)


if __name__ == "__main__":
    text = render()
    if "--check" in sys.argv:
        sys.exit(0 if OUT.exists() and OUT.read_text() == text else 1)
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)}")
