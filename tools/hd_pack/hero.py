"""Hero grain ("detail mode", owner 6 Oct; the hero overlays of the approved pack): real-world CC0 photo grain from
ambientCG on the upscaled world textures of a few material families.

For a pack texture whose name matches a family (RULES, first match wins), the base is kept whole (lines, cracks,
decals, leaves) and only the photo's fine luminance grain is added, scaled by the base's own fine-detail std per
channel: hero = base + strength x (photo luminance high-pass, normalised, clipped to +-2.5) x base fine std.
Wood and bark turn the photo to the texture's own grain direction (structure tensor), and are skipped when the
direction is diagonal, unclear or mixed across an atlas. Skipped: normal/specular maps (by name and by colour),
signs/UI, cutouts (AlphaUsage 1) and textures under 64 px. Checks (a failure keeps the base): mean colour shift
<= 3 levels per channel, fine-detail std ratio 0.9-2.5, alpha identical, no NaN. Output: BC7, box mips, DX10.
Deterministic CPU numpy."""
import re
from pathlib import Path

import numpy as np
from PIL import Image

import dds

SKIP = re.compile(r'NORMAL|NORM|_NRM|SPEC|SPECULAR|GLOSS|_(SP|SPE|N|NO|NOR)$|^SGN_|^QRACE_|SANDWICH|^MINI|^SHD_|^FX_|'
                  r'^FONT|^MARKER_')
# (family, regex on the name, ambientCG material, tiles across the short side, strength, grain direction or None)
RULES = [
    ('cliff', r'CLIFF|ROCK', 'Rock051', 2, 0.8, None),
    ('cobble', r'COBBLE', 'Gravel043', 2, 0.8, None),
    ('gravel', r'GRAVEL', 'Gravel043', 2, 0.8, None),
    ('sand', r'SANDA|_SAND', 'Ground054', 2, 0.8, None),
    ('dirt', r'DIRTPATH|DIRT_|MUD', 'Ground110', 2, 0.8, None),
    ('grass', r'GRASS', 'Grass004', 4, 0.8, None),
    ('road', r'PAVEMENT|ASPHALT|^TRN.*ROAD|^TRNS_ALL_LAB', 'Asphalt031', 1, 0.6, None),
    ('concrete', r'SIDEWALK|CONCRET|GRUNG|BARRIER|OVERPASS', 'Ground054', 2, 0.8, None),
    ('brick', r'BRICK', 'Ground054', 2, 0.8, None),
    ('stone', r'STONE', 'Ground054', 2, 0.8, None),
    ('metal', r'METAL|RUST', 'Metal063', 2, 0.8, None),
    # hero pass 2: the photo's grain turned to the texture's own direction
    ('bark', r'BARK|TRUNK', 'Bark014', 2, 0.8, 'vertical'),
    ('wood', r'WOOD|PLANK', 'Wood051', 1, 0.8, 'horizontal'),
]
MATERIALS = sorted({r[2] for r in RULES})


def photo_name(mat):
    return f'{mat}_2K-JPG_Color.jpg'


def rule_for(name, au=None):
    """The family rule for a texture name, or None (skipped names and cutouts have none)."""
    n = name.lstrip('_')
    if SKIP.search(n) or au == 1:
        return None
    return next((r for r in RULES if re.search(r[1], n)), None)


def blur(a, sigma):
    """Wrapping separable Gaussian; large sigmas run on a downsampled copy (local statistics are smooth)."""
    a = a.astype(np.float32)
    f = int(sigma // 4) if sigma > 8 else 1
    if f > 1:
        h, w = a.shape[:2]
        small = a[:h // f * f, :w // f * f].reshape(h // f, f, w // f, f, *a.shape[2:]).mean((1, 3))
        small = blur(small, sigma / f)
        ys = np.minimum(np.arange(h) // f, small.shape[0] - 1)
        xs = np.minimum(np.arange(w) // f, small.shape[1] - 1)
        return small[ys][:, xs]
    r = max(int(np.ceil(sigma * 3)), 1)
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2).astype(np.float32)
    k /= k.sum()
    for axis in (0, 1):
        out = np.zeros_like(a)
        for i, wgt in enumerate(k):
            out += wgt * np.roll(a, i - r, axis=axis)
        a = out
    return a


def grain(rgb):
    """('horizontal' | 'vertical' | None, coherence, angle in degrees) of the grain lines."""
    lum = blur(rgb.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32), 1.5)
    gx = np.roll(lum, -1, 1) - np.roll(lum, 1, 1)
    gy = np.roll(lum, -1, 0) - np.roll(lum, 1, 0)
    jxx, jyy, jxy = float((gx * gx).mean()), float((gy * gy).mean()), float((gx * gy).mean())
    coh = np.hypot(jxx - jyy, 2 * jxy) / max(jxx + jyy, 1e-6)
    theta = 0.5 * np.degrees(np.arctan2(2 * jxy, jxx - jyy))
    if coh < 0.15:
        return None, coh, theta
    if abs(theta) <= 20:
        return 'vertical', coh, theta
    if abs(abs(theta) - 90) <= 20:
        return 'horizontal', coh, theta
    return None, coh, theta


def photo_tile(photo, w, h, tiles):
    side = min(w, h) / tiles
    nx, ny = max(int(round(w / side)), 1), max(int(round(h / side)), 1)
    tw, th = max(int(round(w / nx)), 8), max(int(round(h / ny)), 8)
    t = np.asarray(photo.resize((tw, th), Image.LANCZOS), np.float32)
    return np.tile(t, (int(np.ceil(h / th)), int(np.ceil(w / tw)), 1))[:h, :w]


def hero_detail(rgb, photo, tiles, strength):
    h, w = rgb.shape[:2]
    pt = photo_tile(photo, w, h, tiles)
    radius = max(min(w, h) / 32, 4)
    fine = max(min(w, h) / 128, 1.5)
    lum = pt @ np.array([0.299, 0.587, 0.114], np.float32)
    hp = lum - blur(lum, fine)
    hp /= np.sqrt(np.maximum(blur(hp * hp, radius), 1e-3))
    hp = np.clip(hp, -2.5, 2.5)   # no sparkle: bright pebbles in a photo would become specks
    b = rgb.astype(np.float32)
    bf = b - blur(b, fine)
    bfs = np.sqrt(np.maximum(blur(bf ** 2, radius), 1.0))
    return np.clip(b + strength * hp[..., None] * bfs, 0, 255).astype(np.uint8)


class Photos:
    """The downloaded CC0 photos (photos/<Material>_2K-JPG_Color.jpg), opened once each."""
    def __init__(self, folder):
        self.folder, self.cache = Path(folder), {}

    def has(self, mat):
        return (self.folder / photo_name(mat)).is_file()

    def get(self, mat, turn=False):
        k = (mat, turn)
        if k not in self.cache:
            p = Image.open(self.folder / photo_name(mat)).convert('RGB')
            self.cache[k] = p.transpose(Image.ROTATE_90) if turn else p
        return self.cache[k]


def apply(dds_path, name, au, photos):
    """Hero grain on one pack DDS, in place. Returns a record: {'hero': family, ...} when written, else
    {'hero_skip': why}."""
    rule = rule_for(name, au)
    if rule is None:
        return {'hero_skip': 'no family'}
    fam, _, mat, tiles, strength, direction = rule
    if not photos.has(mat):
        return {'hero_skip': f'photo {mat} not downloaded'}
    img = np.asarray(Image.open(dds_path).convert('RGBA'))
    h, w = img.shape[:2]
    rgb, alpha = np.ascontiguousarray(img[..., :3]), np.ascontiguousarray(img[..., 3])
    m = rgb.reshape(-1, 3).mean(0)
    if min(w, h) < 64:
        return {'hero_skip': 'small'}
    if m[2] > 170 and abs(m[0] - 128) < 40 and abs(m[1] - 128) < 40:
        return {'hero_skip': 'normal-map colour'}
    rec = {}
    turn = False
    if direction:
        g, coh, theta = grain(rgb)
        if g is None:
            return {'hero_skip': f'grain unclear (coherence {coh:.2f}, angle {theta:.0f})'}
        bh, bw = h // 8, w // 8
        votes = [grain(rgb[y:y + bh, x:x + bw])[0] for y in range(0, bh * 8, bh) for x in range(0, bw * 8, bw)]
        clear = [v for v in votes if v]
        agree = sum(v == g for v in clear) / max(len(clear), 1)
        if agree < 0.95:
            return {'hero_skip': f'mixed grain ({g}, agree {agree:.2f})'}
        turn = g != direction
        rec.update(grain=g, coherence=round(float(coh), 2))
    hero = hero_detail(rgb, photos.get(mat, turn), tiles, strength)
    shift = float(np.abs(hero.reshape(-1, 3).mean(0) - m).max())
    f0 = rgb.astype(np.float32) - blur(rgb, 1.5)
    f1 = hero.astype(np.float32) - blur(hero, 1.5)
    ratio = float(f1.std() / max(f0.std(), 1e-3))
    if not (shift <= 3.0 and 0.9 <= ratio <= 2.5 and np.isfinite(hero).all()):
        return {'hero_skip': f'checks (shift {shift:.2f}, ratio {ratio:.2f})'}
    levels = [(hero, alpha)]
    lw, lh = w, h
    while lw > 1 or lh > 1:
        lw, lh = max(lw // 2, 1), max(lh // 2, 1)
        levels.append((np.asarray(Image.fromarray(hero).resize((lw, lh), Image.BOX)),
                       np.asarray(Image.fromarray(alpha).resize((lw, lh), Image.BOX))))
    import upscale
    dds.write(dds_path, 'bc7', w, h, upscale.encode(levels, 'bc7'))
    rec.update(hero=fam, cc0=mat, cc0_licence='CC0 1.0 (ambientCG)', tiles=tiles, strength=strength,
               shift=round(shift, 2), detail_ratio=round(ratio, 2))
    return rec
