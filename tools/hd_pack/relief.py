#!/usr/bin/env python3
"""Wall relief (3.0): normal maps for the game's "Sunlit wall relief" setting on five facade textures, made on this
Mac from the HD texture pack's albedo (recipe albedo-height-v1). Runs inside Setup's "Make HD textures" step.

    relief.py --hd-cache DIR --out DIR [--photo-cache DIR] [--grain-zip DIR] [--no-grain]

For each texture of relief-keys.txt (our own list: key, family, polarity), at half the albedo's side (2x the game):
  1. luma; height = polarity x band-pass (blur 0.7 px - blur 8 px, wrapping), soft-limited at 2.5 sigma;
  2. normal from Sobel (wrapping), slope scaled to an RMS angle of 8 deg, soft-clipped at 35 deg; DX convention;
  3. fine grain: the ambientCG CC0 NormalDX photo of the family (Ground054; Metal063 for metal; pinned SHA-256, as
     hdpack.py's photos) at 3 deg, blended by reoriented normal mapping; without the photo the step goes on plainly;
  4. mips: the vectors box-filtered unnormalised, detail x0.6 per level below mip 1, renormalised; alpha = the
     family's placeholder gloss x the filtered length. RGBA8 DX10 UNORM, full mip chain.
Writes <out>/<key>.dds, manifest.tsv (ecos1/2/4 and sig1/2/4 from each map's top level: the shader's brightness
terms) and index.tsv ("KEY normal FILE") last. numpy and Pillow. Exit 0 done, 2 skipped (no HD pack yet), 1 failed.
"""
import argparse
import os
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hdpack     # noqa: E402  (stdlib only at import)
import materials  # noqa: E402  (stdlib only at import)

say = materials.say
KEYS_FILE = HERE / 'relief-keys.txt'
RECIPE = 'albedo-height-v1'
GRAIN = {'brick': 'Ground054', 'concrete': 'Ground054', 'stone': 'Ground054', 'metal': 'Metal063'}
GLOSS = {'brick': 0.20, 'concrete': 0.20, 'stone': 0.25, 'metal': 0.45}   # placeholder, not measured
STRUCT_DEG, GRAIN_DEG, CLIP_DEG = 8.0, 3.0, 35.0
SIG_LO, SIG_HI, MORPH_R = 0.7, 8.0, 2
FADE = 0.6
STRENGTHS = (1, 2, 4)
MANIFEST_COLUMNS = ('key', 'name', 'family', 'recipe', 'polarity', 'confidence',
                    'ecos1', 'ecos2', 'ecos4', 'sig1', 'sig2', 'sig4')


# --- grain photos (plain Python) ---

def fetch_grain(out, mats, zip_dir=None):
    """{material: NormalDX jpg path} for those available, kept in out (default <hd-cache>/photos, beside hdpack's
    colour photos)."""
    out.mkdir(parents=True, exist_ok=True)
    got = {}
    for mat in sorted(mats):
        jpg = out / f'{mat}_2K-JPG_NormalDX.jpg'
        if not jpg.is_file():
            want = hdpack.PHOTOS.get(mat)
            local = Path(zip_dir) / f'{mat}_2K-JPG.zip' if zip_dir else None
            z = out / f'.{mat}-relief.zip'
            try:
                if local and local.is_file() and hdpack.sha(local) == want:
                    src = local
                else:
                    hdpack.download(hdpack.PHOTO_URL.format(mat), z, want)
                    src = z
                with zipfile.ZipFile(src) as zf:
                    name = next(n for n in zf.namelist() if n.endswith('_NormalDX.jpg'))
                    tmp = out / f'.{mat}-normal.jpg.part'
                    tmp.write_bytes(zf.read(name))
                    os.replace(tmp, jpg)
            except Exception as e:
                say(f'wall relief: grain photo {mat} not fetched ({str(e)[:80]}); made without it')
            finally:
                if z.exists():
                    z.unlink()
        if jpg.is_file():
            got[mat] = jpg
    return got


# --- the recipe (numpy) ---

def blur(a, sigma):
    import numpy as np
    from PIL import Image
    if sigma > 6:                                   # large sigma on a 2x-downsampled copy
        h, w = a.shape
        if h >= 4 and w >= 4:
            s = a[:h // 2 * 2, :w // 2 * 2].reshape(h // 2, 2, w // 2, 2).mean((1, 3))
            s = blur(s, sigma / 2)
            return np.repeat(np.repeat(s, 2, 0), 2, 1)[:h, :w] if (h % 2 == 0 and w % 2 == 0) else \
                np.asarray(Image.fromarray(s).resize((w, h), Image.BILINEAR))
    r = max(int(np.ceil(sigma * 3)), 1)
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2).astype(np.float32)
    k /= k.sum()
    for ax in (0, 1):
        out = np.zeros_like(a)
        for i, wgt in enumerate(k):
            out += wgt * np.roll(a, i - r, axis=ax)
        a = out
    return a


def rank1(a, r, op, ax):
    import numpy as np
    out = a.copy()
    for d in range(1, r + 1):
        out = op(out, op(np.roll(a, d, ax), np.roll(a, -d, ax)))
    return out


def tail(a, q=0.95):
    import numpy as np
    t = np.quantile(a, q)
    return float(a[a >= t].mean())


def confidence(y):
    """The polarity vote's confidence (thin dark vs thin light lines, directional top-hats at radius 2 px, voted on a
    4x4 grid) and its sign (+1: dark lines low). Recorded only: the shipped polarity decides."""
    import numpy as np
    bt, wt = np.zeros_like(y), np.zeros_like(y)
    for ax in (0, 1):
        closing = rank1(rank1(y, MORPH_R, np.maximum, ax), MORPH_R, np.minimum, ax)
        opening = rank1(rank1(y, MORPH_R, np.minimum, ax), MORPH_R, np.maximum, ax)
        bt, wt = np.maximum(bt, closing - y), np.maximum(wt, y - opening)
    B, W = tail(bt), tail(wt)
    contrast = abs(B - W) / max(B + W, 1e-6)
    h, w = y.shape
    nb_y, nb_x = (4 if h >= 128 else 2 if h >= 64 else 1), (4 if w >= 128 else 2 if w >= 64 else 1)
    votes = []
    for by in range(nb_y):
        for bx in range(nb_x):
            sl = (slice(by * h // nb_y, (by + 1) * h // nb_y), slice(bx * w // nb_x, (bx + 1) * w // nb_x))
            votes.append(tail(bt[sl]) >= tail(wt[sl]))
    return contrast * float(np.mean([v == (B >= W) for v in votes])), (1.0 if B >= W else -1.0)


def down(a):
    """2x box down of an (h, w[, c]) array; a side of 1 stays 1."""
    h, w = a.shape[:2]
    fy, fx = (2 if h > 1 else 1), (2 if w > 1 else 1)
    return a.reshape(h // fy, fy, w // fx, fx, *a.shape[2:]).mean(axis=(1, 3))


def norm(v):
    import numpy as np
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-8)


def rnm(base, detail):
    import numpy as np
    t = base + np.array([0, 0, 1.0], np.float32)
    u = detail * np.array([-1, -1, 1.0], np.float32)
    return norm(t * (np.sum(t * u, axis=-1, keepdims=True) / np.maximum(t[..., 2:3], 1e-6)) - u)


def rms_deg(n):
    import numpy as np
    return float(np.degrees(np.sqrt(np.mean(np.arccos(np.clip(n[..., 2], -1, 1)) ** 2))))


def zero_tilt(n):
    """The mean slope removed (the shader's brightness terms assume a zero mean tilt)."""
    import numpy as np
    s = n[..., :2] / np.maximum(n[..., 2:3], 1e-3)
    s = s - s.reshape(-1, 2).mean(0)
    return norm(np.dstack([s, np.ones(s.shape[:2], np.float32)]))


def scale_xy(n, target_deg):
    """The slope (xy/z) scaled so the RMS angle hits the target."""
    import numpy as np
    s = n[..., :2] / np.maximum(n[..., 2:3], 1e-3)
    m = np.linalg.norm(s, axis=-1)
    k = np.tan(np.radians(target_deg)) / max(float(np.sqrt(np.mean(m ** 2))), 1e-6)
    for _ in range(6):
        out = norm(np.dstack([s * k, np.ones_like(m)]))
        cur = rms_deg(out)
        if cur < 1e-6 or abs(cur - target_deg) < 0.05:
            break
        k *= np.tan(np.radians(target_deg)) / np.tan(np.radians(cur))
    return out


def grain_tile(path, w, h, tiles=2):
    import numpy as np
    from PIL import Image
    with Image.open(path) as im:
        img = im.convert('RGB')
    side = min(w, h) / tiles
    nx, ny = max(int(round(w / side)), 1), max(int(round(h / side)), 1)
    tw, th = max(w // nx, 4), max(h // ny, 4)
    t = np.asarray(img.resize((tw, th), Image.LANCZOS), np.float32) / 255.0
    t = np.tile(t, (int(np.ceil(h / th)), int(np.ceil(w / tw)), 1))[:h, :w]
    return norm(t * 2 - 1)


def structure(y, sign, deg):
    import numpy as np
    hgt = sign * (blur(y, SIG_LO) - blur(y, SIG_HI))
    sd = max(float(hgt.std()), 1e-6)
    hgt = 2.5 * sd * np.tanh(hgt / (2.5 * sd))
    r = np.roll
    gx = (r(hgt, -1, 1) - r(hgt, 1, 1)) * 2 + (r(r(hgt, -1, 1), 1, 0) - r(r(hgt, 1, 1), 1, 0)) \
        + (r(r(hgt, -1, 1), -1, 0) - r(r(hgt, 1, 1), -1, 0))
    gy = (r(hgt, -1, 0) - r(hgt, 1, 0)) * 2 + (r(r(hgt, -1, 0), 1, 1) - r(r(hgt, 1, 0), 1, 1)) \
        + (r(r(hgt, -1, 0), -1, 1) - r(r(hgt, 1, 0), -1, 1))
    n = scale_xy(np.dstack([-gx, -gy, np.ones_like(hgt)]).astype(np.float32), deg)   # DX: +y = image down
    s = n[..., :2] / n[..., 2:3]
    m = np.linalg.norm(s, axis=-1, keepdims=True)
    mc = np.tan(np.radians(CLIP_DEG))
    s = s * (mc * np.tanh(m / mc) / np.maximum(m, 1e-8))
    return norm(np.dstack([s, np.ones_like(m)]))


def build_levels(st, gr, gloss):
    """RGBA8 bytes per level, top first, down to 1x1."""
    import numpy as np
    levels, level = [], 0
    while True:
        amp = 1.0 if level <= 1 else FADE ** (level - 1)
        s, g = st.copy(), gr.copy()
        tok = np.clip(np.linalg.norm(st, axis=-1) * np.linalg.norm(gr, axis=-1), 0, 1)
        s[..., :2] *= amp
        g[..., :2] *= amp
        n = rnm(norm(s), norm(g))
        rgba = np.dstack([np.clip((n * 0.5 + 0.5) * 255 + 0.5, 0, 255),
                          np.clip(gloss * tok * 255 + 0.5, 0, 255)]).astype(np.uint8)
        levels.append(np.ascontiguousarray(rgba).tobytes())
        if st.shape[0] <= 1 and st.shape[1] <= 1:
            break
        st, gr = down(st), down(gr)
        level += 1
    return levels


def ecos(n, s):
    import numpy as np
    v = n.copy()
    v[..., :2] *= s
    return float(np.mean(v[..., 2] / np.linalg.norm(v, axis=-1)))


def sigma(n, s):
    """Per-axis RMS of the tangential part of the normalised, strength-scaled normal."""
    import numpy as np
    v = n.copy()
    v[..., :2] *= s
    v = v / np.linalg.norm(v, axis=-1, keepdims=True)
    return float(np.sqrt(np.mean(v[..., 0] ** 2 + v[..., 1] ** 2) / 2))


def terms(top, w, h):
    """ecos1/2/4 and sig1/2/4 of a map's top level (RGBA8 bytes), as the shader's manifest wants them."""
    import numpy as np
    n = np.frombuffer(top, np.uint8).reshape(h, w, 4)[..., :3].astype(np.float32) / 255 * 2 - 1
    return [ecos(n, s) for s in STRENGTHS] + [sigma(n, s) for s in STRENGTHS]


def make_one(row, src, out, grain):
    """One key: albedo DDS -> <out>/<key>.dds. Returns (confidence, terms, size)."""
    import numpy as np
    from PIL import Image
    import dds
    with Image.open(src) as im:
        im.load()
        a = np.asarray(im.convert('RGB')).astype(np.float32) / 255.0
    rgb = down(a) if min(a.shape[:2]) >= 2 else a
    y = (rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32)).astype(np.float32)
    h, w = y.shape
    conf, _ = confidence(y)
    sign = 1.0 if row['polarity'] == 'dark_low' else -1.0
    st = zero_tilt(structure(y, sign, STRUCT_DEG))
    g = grain.get(GRAIN[row['family']])
    gr = zero_tilt(scale_xy(grain_tile(g, w, h), GRAIN_DEG)) if g else \
        np.dstack([np.zeros((h, w, 2), np.float32), np.ones((h, w), np.float32)])
    levels = build_levels(st, gr, GLOSS[row['family']])
    dds.write(out / f"{row['key']}.dds", 'rgba8', w, h, levels)
    return conf, terms(levels[0], w, h), (w, h)


def run(hd, out, grain_zip=None, use_grain=True, keys_file=KEYS_FILE, photo_cache=None):
    pack = materials.pack_dir(hd)
    if pack is None:
        say('wall relief skipped (the HD pack is not finished)')
        return 2
    rows = materials.read_keys(keys_file, ('key', 'family', 'polarity'))
    files = materials.albedo_files(pack)
    have = [r for r in rows if r['key'] in files]
    if not have:
        say('wall relief skipped (none of its textures is in the HD pack)')
        return 2
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    grain = fetch_grain(photo_cache or hd / 'photos', {GRAIN[r['family']] for r in have}, grain_zip) if use_grain else {}
    names = materials.names_of(hd)
    man, idx = ['\t'.join(MANIFEST_COLUMNS)], []
    for r in have:
        try:
            conf, t, _ = make_one(r, files[r['key']], out, grain)
        except Exception as e:
            say(f"wall relief: {r['key']} not made ({str(e)[:80]})")
            continue
        recipe = RECIPE + ('' if GRAIN[r['family']] in grain else ' (no grain)')
        man.append('\t'.join([r['key'], names.get(r['key'], '-'), r['family'], recipe, r['polarity'],
                              f'{conf:.3f}'] + [f'{v:.5f}' for v in t]))
        idx.append(f"{r['key']}\tnormal\t{r['key']}.dds")
    if not idx:
        say('wall relief: stopped (no normal map made)')
        return 1
    (out / 'manifest.tsv').write_text('\n'.join(man) + '\n')
    materials.write_index(out, f'# Wall relief ({RECIPE}, RGBA8 DX10): key, slot, file', idx)
    say(f'wall relief ready ({len(idx)} normal map{"s" if len(idx) != 1 else ""}; {time.time() - t0:.0f} s)')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--hd-cache', type=Path, help='the HD step\'s folder (default as hdpack.py)')
    ap.add_argument('--out', type=Path, required=True, help='the Wall relief folder to make')
    ap.add_argument('--grain-zip', type=Path, help='a folder holding the pinned ambientCG zips (no download)')
    ap.add_argument('--photo-cache', type=Path, help='where the grain photo is kept (default <hd-cache>/photos)')
    ap.add_argument('--no-grain', action='store_true', help='no grain photo (no network)')
    a = ap.parse_args(argv)
    hd = hdpack.cache_root(a.hd_cache)
    if materials.pack_dir(hd) is None:
        say('wall relief skipped (the HD pack is not finished)')
        return 2
    rc = materials.ensure_env(hd, ('numpy', 'PIL'), 'wall relief')
    if rc is not None:
        return rc
    try:
        return run(hd, a.out.expanduser(), a.grain_zip, not a.no_grain, photo_cache=a.photo_cache)
    except Exception as e:   # never a traceback in the player's setup window
        say(f'wall relief stopped: {str(e)[:160]}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
