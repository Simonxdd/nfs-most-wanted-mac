#!/usr/bin/env python3
"""HD HUD, picture layer ("ui"): the race HUD, minimap icons and fonts at 4x, made from the player's own game files.

    ui_pack.py --game DIR [--stuff DIR] --out DIR [--tiles]

Inputs (read only): GLOBAL/HUDTEXRACE.BIN, HUDS_Custom_00.bin (the default gauge set), the minimap icons, markers
and backings in InGameA.bun / GlobalB.lzc, the fonts in GLOBALA.BUN and FRONTEND/FrontB.lzc (the Xbox 360 Stuff
Pack's FrontB.lzc when --stuff names a folder holding one); with --tiles also every TRACKS/L2RA/MINI_MAP*.BIN tile
(off by default: about 1.8 GB and no measured gain).

Recipe (no model, no network):
  alpha textures: colour bled into transparent texels, then Lanczos 4x; alpha upsampled smoothly, then every STEEP
    edge (an anti-aliased outline rising over about one source texel) is redrawn at 4x between its local low and
    high level, its mid-level re-anti-aliased over ~1.2 output pixels; gentle gradients keep the smooth upsample.
  opaque textures: Lanczos 4x + a mild unsharp mask.
  tiles: each MINI_MAP file's 64 tiles stitched 8 x 8 (entry order), Lanczos 4x + unsharp mask, re-cut; equal output
    tiles share one file.
Mips: box filter, colour and alpha apart. Formats: RGBA8 for fonts, masks, LEDs and needles; BC7 for the rest.
Index rows "<key>\\talbedo\\t<file>" (tiles: slot albedo_ondemand), read by the core mod (remaster_tex.h).

Resumable: finished textures are recorded in <out>/.done.jsonl and skipped on the next run while their source is
unchanged. index.tsv is written last, only when everything is made. Exit 0 done, 1 failed.
Progress lines start with "HUD textures:"."""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dds       # noqa: E402
import texread   # noqa: E402

PREFIX = 'HUD textures: '
RECIPE = 'ui-v1'        # recorded with each texture; a record with another recipe is made again
SCALE = 4
MIN_SIDE = 16
HUD_FILES = {
    'GLOBAL/HUDTEXRACE.BIN': None,          # every texture
    'GLOBAL/HUDS_Custom_00.bin': None,      # the default gauge set
    'GLOBAL/InGameA.bun': re.compile(r'^(MINIMAP_|ARROW|BLACK_RING$|BUSTED_ICON$|CAR_MARKER|CHECKMARK_ICON$|'
                                     r'CORNER_CROSSHAIR$|GENERIC_EMPTY_CHECK$|HEAT_ICON|HELICOPTER_MINIMAP_ICON$|'
                                     r'INFO_FLASHER_PANEL$|INNER_STRIPES$|MIRROR_BACKING_RADAR$|N20_ICON$|NUMBER2$|'
                                     r'OUTRUN_BACKING$|OUTTER_RING_PIECE$|PERSUIT_ICON$|POSITION_BACKING$|RVM_MASK$|'
                                     r'SAFEHOUSE_ICON$|SHIFT_UP_ICON$|SMALL_CIRCLE$|TIMER_ICON$|TIME_BACKING$)'),
    'GLOBAL/GlobalB.lzc': re.compile(r'^(MINIMAP_BACKING_COLOR|ARROW_MAIN|ARROW_SKINNIER|COPS_DAMAGED_ICON|'
                                     r'COPS_TAKENOUT_ICON|OUTTER_RING|BLACK_CIRCLE_BASE|BUTTON_RING|LED_MAIN)$'),
    'GLOBAL/GLOBALA.BUN': re.compile(r'^FONT_'),
    'FRONTEND/FrontB.lzc': re.compile(r'^FONT_'),
}
RGBA8 = re.compile(r'^(FONT_|MINIMAP_MASK$|RADAR_LEDS|LED_MAIN$|TACH_NEEDLE|TURBO_NEEDLE|REDLINE_MASK$|HEATMETERMASK)')


def say(msg):
    print(PREFIX + msg, flush=True)


def resolve_ci(base, rel):
    p = Path(base)
    for part in rel.split('/'):
        if not p.is_dir():
            return None
        p = next((c for c in sorted(p.iterdir()) if c.name.lower() == part.lower()), None)
        if p is None:
            return None
    return p


def stuff_frontb(stuff):
    """The Stuff Pack's FRONTEND/FrontB.lzc under one of its usual folders, or None."""
    cands = [Path(stuff)]
    try:
        sys.path.insert(0, str(HERE.parent / 'setup_kit'))
        import x360_stuff
        cands = x360_stuff.roots(stuff) + cands
    except Exception:
        pass
    for c in cands:
        f = resolve_ci(c, 'FRONTEND/FrontB.lzc')
        if f is not None and f.is_file():
            return f
    return None


def inputs(game, stuff=None):
    """[(relative name, path)] of the HUD files; a missing one raises FileNotFoundError."""
    out = []
    front = stuff_frontb(stuff) if stuff else None
    for rel in HUD_FILES:
        p = front if (rel == 'FRONTEND/FrontB.lzc' and front is not None) else resolve_ci(game, rel)
        if p is None or not p.is_file():
            raise FileNotFoundError(f'{rel} not found in the game folder')
        out.append((rel, p))
    return out


# --- the recipe (numpy, Pillow) ---

def classify(a, au):
    """'shape' when the alpha is a hard shape with a thin anti-aliased edge; 'soft' otherwise; 'opaque' when none."""
    import numpy as np
    if au == 0 or a.min() > 250:
        return 'opaque'
    f = a.astype(np.float32) / 255
    mid = ((f > 0.08) & (f < 0.92)).sum()
    solid = (f >= 0.5).sum()
    if solid == 0 or (f < 0.08).sum() == 0:
        return 'soft'
    return 'shape' if mid / solid < 0.35 else 'soft'


def bleed(rgb, a):
    """Colour of the nearest visible texels pushed into transparent ones (so upsampling does not drag black in)."""
    import numpy as np
    rgb = rgb.astype(np.float32)
    w = (a > 8).astype(np.float32)
    if w.sum() == 0:
        return rgb.astype(np.uint8)
    acc, wt = rgb * w[..., None], w.copy()

    def k(x):
        return np.roll(x, 1, 0) + np.roll(x, -1, 0) + np.roll(x, 1, 1) + np.roll(x, -1, 1) + x
    for _ in range(8):
        if (wt > 0).all():
            break
        acc, wt = k(acc), k(wt)
    fill = np.where(wt[..., None] > 0, acc / np.maximum(wt, 1e-6)[..., None], rgb)
    out = np.where(w[..., None] > 0, rgb, fill)
    return np.clip(out, 0, 255).astype(np.uint8)


def up(arr, size):
    import numpy as np
    from PIL import Image
    return np.asarray(Image.fromarray(np.ascontiguousarray(arr)).resize(size, Image.LANCZOS))


def redraw_alpha(a, size, scale=SCALE):
    """The edge redraw: alpha upsampled smoothly (bicubic, float); every steep edge is redrawn between its local low
    and high level, gentle gradients keep the smooth upsample. Returns (alpha, share of texels changed by > 2/255)."""
    import numpy as np
    from PIL import Image, ImageFilter
    A = np.clip(np.asarray(Image.fromarray(a.astype(np.float32) / 255).resize(size, Image.BICUBIC)), 0, 1)
    r = 2 * scale + 1                                  # ~2 source texels around each output pixel
    A8 = Image.fromarray((A * 255).astype(np.uint8))
    hi = np.asarray(A8.filter(ImageFilter.MaxFilter(r))).astype(np.float32) / 255
    lo = np.asarray(A8.filter(ImageFilter.MinFilter(r))).astype(np.float32) / 255
    gy, gx = np.gradient(A)
    g = np.hypot(gx, gy) * scale                       # alpha change per source texel
    span = hi - lo
    t = np.clip((A - lo) / np.maximum(span, 1e-4), 0, 1)
    w = 0.6 / scale                                    # half-width of the redrawn edge, in units of the local span
    u = np.clip((t - (0.5 - w)) / (2 * w), 0, 1)
    sharp = lo + span * u * u * (3 - 2 * u)
    steep = np.clip((g - 0.25) / 0.2, 0, 1) * (span > 0.3)
    # spread the steep mask over the edge's width, so the whole transition is redrawn
    steep = np.asarray(Image.fromarray((steep * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(scale + 1)))
    k = steep.astype(np.float32) / 255
    out = A + (sharp - A) * k
    out8 = np.clip(out * 255 + 0.5, 0, 255).astype(np.uint8)
    changed = float((np.abs(out8.astype(np.int16) - np.clip(A * 255 + 0.5, 0, 255).astype(np.int16)) > 2).mean())
    return out8, changed


def upscale(rgba, cls, scale=SCALE):
    """(rgb, alpha, redrawn share) at scale x."""
    import numpy as np
    from PIL import Image, ImageFilter
    h, w = rgba.shape[:2]
    size = (w * scale, h * scale)
    rgb, a = np.ascontiguousarray(rgba[..., :3]), np.ascontiguousarray(rgba[..., 3])
    if cls == 'opaque':
        r = Image.fromarray(rgb).resize(size, Image.LANCZOS)
        r = r.filter(ImageFilter.UnsharpMask(radius=2.0, percent=60, threshold=2))
        return np.asarray(r), np.full(size[::-1], 255, np.uint8), 0.0
    rgb4 = up(bleed(rgb, a), size)
    return (rgb4,) + redraw_alpha(a, size, scale)


def mips(rgb, a):
    """Full chain, box filter from the top level, colour and alpha apart."""
    import numpy as np
    from PIL import Image
    levels = [(rgb, a)]
    h, w = rgb.shape[:2]
    while w > 1 or h > 1:
        w, h = max(w // 2, 1), max(h // 2, 1)
        levels.append((np.asarray(Image.fromarray(levels[0][0]).resize((w, h), Image.BOX)),
                       np.asarray(Image.fromarray(levels[0][1]).resize((w, h), Image.BOX))))
    return levels


def encode(levels, fmt):
    import numpy as np
    out = []
    for r, a in levels:
        h, w = r.shape[:2]
        rgba = np.ascontiguousarray(np.dstack([r, a]))
        if fmt == 'rgba8':
            out.append(rgba.tobytes())
            continue
        import etcpak
        if w % 4 or h % 4:
            rgba = np.ascontiguousarray(np.pad(rgba, ((0, (-h) % 4), (0, (-w) % 4), (0, 0)), mode='edge'))
        out.append(etcpak.compress_bc7(rgba.tobytes(), rgba.shape[1], rgba.shape[0]))
    return out


def write(outdir, name, rgb, a, fmt):
    p = Path(outdir) / name
    dds.write(p, fmt, rgb.shape[1], rgb.shape[0], encode(mips(rgb, a), fmt))
    return p


def fmt_for(name):
    return 'rgba8' if RGBA8.search(name) else 'bc7'


# --- resume records and the index ---

def src_sha(t):
    h = hashlib.sha256(t['fmt'].encode() + (t['top'] or b''))
    if t.get('palette'):
        h.update(t['palette'])
    return h.hexdigest()


def load_done(out, recipe=RECIPE):
    done = {}
    p = out / '.done.jsonl'
    if p.is_file():
        for line in p.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get('recipe') == recipe:
                done[r['id']] = r
    return done


def append_done(out, rec, recipe=RECIPE):
    with open(out / '.done.jsonl', 'a') as f:
        f.write(json.dumps(dict(rec, recipe=recipe)) + '\n')


def file_ok(p, w=None, h=None):
    try:
        _fmt, fw, fh, _lv, _n = dds.read_header(p)
    except (OSError, ValueError):
        return False
    return w is None or (fw, fh) == (w, h)


def write_index(out, rows, title):
    tmp = out / '.index.tsv.part'
    with open(tmp, 'w') as f:
        f.write(f'# {title}\n')
        for k, slot, fn in rows:
            f.write(f'{k}\t{slot}\t{fn}\n')
    os.replace(tmp, out / 'index.tsv')


# --- the build ---

def build_hud(files, out, done, manifest, index):
    import numpy as np
    todo = []
    seen = set()
    for rel, path in files:
        pat = HUD_FILES[rel]
        for t in texread.textures(path):
            if (pat and not pat.search(t['name'])) or t['top'] is None or t['key'] in seen:
                continue
            if min(t['w'], t['h']) < MIN_SIDE:
                manifest['skipped'].append(dict(key=t['key'], name=t['name'], why=f'under {MIN_SIDE} px'))
                continue
            seen.add(t['key'])
            todo.append((rel, t))
    made = 0
    for i, (rel, t) in enumerate(todo, 1):
        fn, fmt = f"{t['key']}.dds", fmt_for(t['name'])
        w4, h4 = t['w'] * SCALE, t['h'] * SCALE
        s = src_sha(t)
        rec = done.get(t['key'])
        if not (rec and rec['src'] == s and rec['fmt'] == fmt and file_ok(out / fn, w4, h4)):
            rgba = np.asarray(texread.to_rgba(t), dtype=np.uint8)
            cls = classify(rgba[..., 3], t['alpha_usage'])
            rgb4, a4, changed = upscale(rgba, cls)
            write(out, fn, rgb4, a4, fmt)
            rec = dict(id=t['key'], src=s, fmt=fmt, file=fn, name=t['name'], rel=rel, cls=cls,
                       redrawn_share=round(changed, 4), size=[w4, h4], source_size=[t['w'], t['h']])
            append_done(out, rec)
            made += 1
        index.append((t['key'], 'albedo', fn))
        manifest['textures'].append(dict(key=t['key'], name=t['name'], file=rel, source_size=[t['w'], t['h']],
                                         cls=rec['cls'], redrawn_share=rec['redrawn_share'],
                                         method='lanczos' if rec['cls'] == 'opaque' else 'edge-redraw',
                                         size=[w4, h4], format=fmt))
        if i % 10 == 0 or i == len(todo):
            say(f'HD HUD pictures {i}/{len(todo)}')
    return len(todo), made


def build_tiles(game, out, done, manifest, index):
    import numpy as np
    from PIL import Image, ImageFilter
    tdir = out / 'tiles'
    tdir.mkdir(exist_ok=True)
    d = resolve_ci(game, 'TRACKS/L2RA')
    srcs = sorted((p for p in d.iterdir() if p.name.upper().startswith('MINI_MAP') and p.suffix.upper() == '.BIN'),
                  key=lambda p: p.name.upper()) if d is not None else []
    if not srcs:
        raise FileNotFoundError('TRACKS/L2RA/MINI_MAP*.BIN not found in the game folder')
    files = set()
    T = 128 * SCALE
    for i, src in enumerate(srcs, 1):
        fsha = hashlib.sha256(src.read_bytes()).hexdigest()
        rid = 'tiles:' + src.name.upper()
        rec = done.get(rid)
        if not (rec and rec['src'] == fsha and all(file_ok(out / fn) for _k, fn in rec['rows'])):
            ts = texread.textures(src)
            if len(ts) != 64:
                raise ValueError(f'{src.name}: {len(ts)} tiles, expected 64')
            grid = Image.new('RGBA', (1024, 1024))
            for n, t in enumerate(ts):   # the tile's place is its entry order (CHOPn row-major)
                grid.paste(texread.to_rgba(t), ((n % 8) * 128, (n // 8) * 128))
            g = np.asarray(grid)
            has_alpha = bool((g[..., 3] < 255).any())
            rgb = Image.fromarray(bleed(g[..., :3], g[..., 3]) if has_alpha else np.ascontiguousarray(g[..., :3]))
            big = rgb.resize((1024 * SCALE, 1024 * SCALE), Image.LANCZOS)
            big = np.asarray(big.filter(ImageFilter.UnsharpMask(radius=2.0, percent=60, threshold=2)))
            bigA = up(g[..., 3], (1024 * SCALE, 1024 * SCALE)) if has_alpha else None
            rows = []
            for n, t in enumerate(ts):
                sl = (slice((n // 8) * T, (n // 8 + 1) * T), slice((n % 8) * T, (n % 8 + 1) * T))
                tile = np.ascontiguousarray(big[sl])
                ta = np.ascontiguousarray(bigA[sl]) if has_alpha else np.full(tile.shape[:2], 255, np.uint8)
                h = hashlib.sha1(tile.tobytes() + ta.tobytes()).hexdigest()[:20]
                fn = f'tiles/{h}.dds'
                if not file_ok(out / fn):
                    write(out, fn, tile, ta, 'bc7')
                rows.append((t['key'], fn))
            rec = dict(id=rid, src=fsha, rows=rows)
            append_done(out, rec)
        for k, fn in rec['rows']:
            index.append((k, 'albedo_ondemand', fn))   # tiles load when first bound (kit remaster_tex.h)
            files.add(fn)
        if i % 20 == 0 or i == len(srcs):
            say(f'HD HUD minimap tiles {i}/{len(srcs)} files')
    manifest['tiles'] = dict(files=len(srcs), keys=sum(1 for r in index if r[1] == 'albedo_ondemand'),
                             unique_files=len(files))


def build(game, out, stuff=None, tiles=False):
    """Make the picture layer in out; returns 0 when index.tsv was written."""
    t0 = time.time()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    files = inputs(game, stuff)
    done = load_done(out)
    manifest = dict(pack='ui-v1', recipe=RECIPE, scale=SCALE, tiles=tiles, textures=[], skipped=[])
    index = []
    n, made = build_hud(files, out, done, manifest, index)
    say(f'HD HUD pictures: {n} textures ({made} made, {n - made} kept from before)')
    if tiles:
        build_tiles(game, out, done, manifest, index)
    elif (out / 'tiles').is_dir():
        shutil.rmtree(out / 'tiles')   # an earlier run's tiles; the option is off now
    manifest['seconds'] = round(time.time() - t0, 1)
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=1) + '\n')
    write_index(out, index, 'HD HUD, picture layer (ui-v1 recipe): tools/hd_pack/ui_pack.py')
    say(f'HD HUD pictures done: {len(index)} index rows, {manifest["seconds"]} s')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--game', type=Path, required=True, help='your game folder (read only)')
    ap.add_argument('--stuff', type=Path, help='a folder holding the Xbox 360 Stuff Pack download (optional)')
    ap.add_argument('--out', type=Path, required=True, help='the output folder')
    ap.add_argument('--tiles', action='store_true', help='also the minimap tiles (about 1.8 GB; off by default)')
    a = ap.parse_args(argv)
    if (a.out / 'index.tsv').is_file():
        (a.out / 'index.tsv').unlink()   # written again at the end; never a half-made layer with an index
    try:
        return build(a.game, a.out, a.stuff, a.tiles)
    except Exception as e:   # never a traceback in the player's setup window
        say(f'HD HUD pictures stopped: {str(e)[:160]}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
