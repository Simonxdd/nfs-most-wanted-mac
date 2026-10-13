#!/usr/bin/env python3
"""HD HUD, drawn layer ("ui2"): 40 HUD textures redrawn as vector art (our own SVGs in hud_svg/), rendered at 4x.

    ui2_pack.py --ui DIR --out DIR [--cache DIR]

--ui is the finished picture layer (ui_pack.py's output; this layer is read after it, so a row here wins and every
texture not listed keeps its picture-layer row). The renderer hud_svg/svgrender.swift is built with swiftc (the Xcode
Command Line Tools) into the cache (default: the folder holding --out) and draws each SVG at 4x through macOS's
own CoreSVG. Finish as the picture layer: colour bled into transparent texels, box mips colour and alpha apart,
RGBA8 for lines, masks, LEDs and needles, BC7 for the rest. No model, no network, no game files.

Desk check before the index: 40 files, each at its expected size and format, alpha coverage within 5 % of the
manifest's (hud_svg/manifest.json). Resumable (<out>/.done.jsonl); index.tsv rows "<key>\\talbedo\\t<file>" are
written last, only when the check passes. Exit 0 done, 1 failed. Progress lines start with "HUD textures:"."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dds       # noqa: E402
import ui_pack   # noqa: E402

SVG_DIR = HERE / 'hud_svg'
MANIFEST = SVG_DIR / 'manifest.json'
RENDERER = SVG_DIR / 'svgrender.swift'
RECIPE = 'ui-v2'
SCALE = 4
COVERAGE_TOL = 0.05     # relative, with a floor of 0.002 absolute for the sparse textures
say = ui_pack.say


def sha(b):
    return hashlib.sha256(b).hexdigest()


def load_manifest():
    return json.loads(MANIFEST.read_text())


def check_svgs(m):
    """Names whose SVG is missing or whose sha256 differs from the manifest."""
    bad = []
    for t in m['textures']:
        p = SVG_DIR / t['svg']
        if not p.is_file() or sha(p.read_bytes()) != t['svg_sha256']:
            bad.append(t['name'])
    return bad


def build_renderer(cache):
    """The svgrender binary in cache, rebuilt when the Swift source changes."""
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    exe, stamp = cache / 'svgrender', cache / 'svgrender.sha256'
    want = sha(RENDERER.read_bytes())
    if exe.is_file() and stamp.is_file() and stamp.read_text() == want:
        return exe
    tmp = cache / '.svgrender.part'
    r = subprocess.run(['xcrun', 'swiftc', '-O', '-module-cache-path', str(cache / '.swift-cache'),
                        '-o', str(tmp), str(RENDERER)], capture_output=True, text=True)
    if r.returncode or not tmp.is_file():
        raise RuntimeError('could not build the SVG renderer (swiftc): ' + (r.stderr or r.stdout).strip()[-200:])
    os.replace(tmp, exe)
    stamp.write_text(want)
    return exe


def render(exe, svg, w, h):
    """(h, w, 4) uint8 RGBA, straight alpha."""
    import numpy as np
    with tempfile.TemporaryDirectory() as td:
        raw = Path(td) / 'out.rgba'
        r = subprocess.run([str(exe), str(svg), str(raw), str(w), str(h)], capture_output=True, text=True)
        data = raw.read_bytes() if raw.is_file() else b''
    if r.returncode or len(data) != w * h * 4:
        raise RuntimeError(f'{Path(svg).name}: render failed {r.stderr.strip()[-120:]}')
    return np.frombuffer(data, np.uint8).reshape(h, w, 4).copy()


def coverage(a):
    return float(a.mean()) / 255


def coverage_ok(got, want):
    return abs(got - want) <= max(COVERAGE_TOL * want, 0.002)


def desk_check(out, m, cov):
    """Problems with the made files (empty when all is well)."""
    probs = []
    if len(m['textures']) != 40:
        probs.append(f"{len(m['textures'])} textures in the manifest, expected 40")
    for t in m['textures']:
        p = out / f"{t['key']}.dds"
        try:
            fmt, w, h, _lv, _n = dds.read_header(p)
        except (OSError, ValueError) as e:
            probs.append(f"{t['name']}: {e}")
            continue
        if [w, h] != t['size'] or fmt != t['format']:
            probs.append(f"{t['name']}: {w}x{h} {fmt}, expected {t['size'][0]}x{t['size'][1]} {t['format']}")
        c = cov.get(t['key'])
        if c is None or not coverage_ok(c, t['alpha_coverage']):
            probs.append(f"{t['name']}: alpha coverage {c}, expected {t['alpha_coverage']}")
    return probs


def build(ui, out, cache=None):
    ui, out = Path(ui), Path(out)
    if not (ui / 'index.tsv').is_file():
        say('HD HUD drawing layer: needs the finished picture layer first (no index.tsv in the ui folder)')
        return 1
    m = load_manifest()
    bad = check_svgs(m)
    if bad:
        say(f'HD HUD drawing layer: SVGs missing or changed: {", ".join(bad[:5])}')
        return 1
    out.mkdir(parents=True, exist_ok=True)
    if (out / 'index.tsv').is_file():
        (out / 'index.tsv').unlink()
    exe = build_renderer(cache or out.parent)
    done = ui_pack.load_done(out, RECIPE)
    cov, index, made = {}, [], 0
    for i, t in enumerate(m['textures'], 1):
        fn = f"{t['key']}.dds"
        w, h = t['size']
        rec = done.get(t['key'])
        if not (rec and rec['svg'] == t['svg_sha256'] and rec['fmt'] == t['format'] and ui_pack.file_ok(out / fn, w, h)):
            rgba = render(exe, SVG_DIR / t['svg'], w, h)
            a = rgba[..., 3].copy()
            rgb = ui_pack.bleed(rgba[..., :3], a)
            ui_pack.write(out, fn, rgb, a, t['format'])
            rec = dict(id=t['key'], svg=t['svg_sha256'], fmt=t['format'], file=fn,
                       coverage=round(coverage(a), 5))
            ui_pack.append_done(out, rec, RECIPE)
            made += 1
        cov[t['key']] = rec['coverage']
        index.append((t['key'], 'albedo', fn))
        if i % 10 == 0 or i == len(m['textures']):
            say(f'HD HUD drawings {i}/{len(m["textures"])}')
    probs = desk_check(out, m, cov)
    if probs:
        say(f'HD HUD drawing layer: desk check failed ({len(probs)}): ' + '; '.join(probs[:3]))
        return 1
    ui_pack.write_index(out, index, 'HD HUD, drawing layer (ui-v2: our own SVGs): tools/hd_pack/ui2_pack.py')
    say(f'HD HUD drawings done: {len(index)} textures ({made} made), desk check passed')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ui', type=Path, required=True, help="the picture layer's folder (ui_pack.py --out)")
    ap.add_argument('--out', type=Path, required=True, help='the output folder')
    ap.add_argument('--cache', type=Path, help='where the renderer is built (default: the folder holding --out)')
    a = ap.parse_args(argv)
    try:
        return build(a.ui, a.out, a.cache)
    except Exception as e:   # never a traceback in the player's setup window
        say(f'HD HUD drawing layer stopped: {str(e)[:160]}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
