"""The HD texture pack's per-texture recipe (2.1; the recipe of the owner-approved pack r1-world-v6b, read from the
player's own game files instead of runtime dumps).

Per texture (process_one):
  1. skipped when the short side is under 32 px (lookup tables, gradients); a normal map when the mean colour is
     blue-violet;
  2. alpha class from the game's AlphaUsage (0/1/2 -> none/cutout/mask), else from the pixels; a cutout with no
     transparent texel is opaque;
  3. method (method_for): Lanczos for signs, markers, HUD, effects, shadows, fonts and the minimap (by name), for the
     cars (by name, by class, and every texture of CARS/*/TEXTURES.BIN: the model invents texture inside dark car
     atlases), else the model 4x-PBRify-UpscalerV4 (CC0) on the GPU in 192 px tiles with 8 px overlap, after a 16 px
     pad that wraps on the axes the tiling bits say and repeats the edge otherwise;
     Lanczos: PIL LANCZOS 4x + UnsharpMask (radius 2, 40 %, threshold 2);
  4. scale 4x when the longer side is under 1024 px, else 2x; halved again until the longer side is at most 4096;
     normal maps renormalised to unit vectors;
  5. alpha resampled on its own with Lanczos; cutouts thresholded at 128 at every mip;
  6. BC1 when opaque and not a normal map, else BC7; mips by box filter in gamma space, colour and alpha apart;
     etcpak encodes, odd sizes edge-padded to whole 4x4 blocks; DX10 DDS, UNORM.
Model-path textures also record two mosaic tripwires (run 26): the high-frequency ratio against a plain Lanczos 4x
and the 16 px block-edge score. torch and etcpak are imported only where they are used."""
import hashlib
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

import dds

# Q1 (owner default, 12 Oct): one model only. The 30 signs run 26 kept from 4xNomos2 (CC-BY-4.0) take the Lanczos
# path like every other SGN_ texture. Keeping them would need that model's pinned download, its NOTICE line and the
# kept-key list; not implemented.
KEEP_NOMOS2_SIGNS = False

LANCZOS_PREFIXES = ('SGN_', 'MINI', 'MARKER_', 'QRACE_', 'FX_', 'SHD_', 'FONT', 'MINIMAP_')
CAR_PREFIXES = ('OBJ_CAR_', 'TRAF')
CAR_CLASS = '1b81e7b0'
# Stage (b) of v6b: from the streamed world (TRACKS/*.BUN) these are left to the game (shadows, dummies, effects,
# fonts, the minimap).
STREAM_SKIP = ('SHD_', 'DUMMY', 'FX_', 'FONT', 'MINI', 'MINIMAP_')
ALWAYS_SKIP = ('DUMMY',)
MIN_SIDE = 32
PAD, TILE, SMALL_TILE, OVERLAP, CAP = 16, 192, 128, 8, 4096
MOSAIC_HF, MOSAIC_BLOCK = 1.6, 1.15


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def group_of(rel):
    """Where a texture comes from, by its file's path relative to the game folder (any case): 'cars' (CARS/*/
    TEXTURES.BIN), 'stuff' (TRACKS/TexWizardX360/textures.bin, the Xbox 360 Stuff Pack), 'stream' (TRACKS/*.BUN,
    the streamed world), else 'global' (GLOBAL, FRONTEND, the minimap)."""
    parts = [p.upper() for p in Path(rel).parts]
    if len(parts) >= 2 and 'TEXWIZARDX360' in parts[:-1]:
        return 'stuff'
    if parts and parts[0] == 'CARS' and parts[-1] == 'TEXTURES.BIN':
        return 'cars'
    if len(parts) == 2 and parts[0] == 'TRACKS' and parts[1].endswith('.BUN'):
        return 'stream'
    return 'global'


# Owner D-169 (12): only the textures of the pack the owner looked at (R1-dev's r1-world-v6b): its texture keys as a
# hashes-only list (name hash << 32; no names, no pixels, like the Mods folder's known.tsv). A key not listed is left to
# the game, so a player never gets an upscale nobody has looked at. None = no list (tests of the other rules).
APPROVED_FILE = Path(__file__).resolve().parent / 'approved-keys.txt'


def approved_keys():
    try:
        return {l.strip().lower() for l in APPROVED_FILE.read_text().splitlines() if l.strip() and not l.startswith('#')}
    except OSError:
        return None


APPROVED = approved_keys()


def selected(info, approved=APPROVED):
    """(True, '') when the texture goes into the pack, else (False, why). info: key, name, w, h, fmt, group."""
    n = info.get('name', '').lstrip('_')
    if approved is not None and str(info.get('key', '')).lower() not in approved:
        return False, 'not in the approved set (R1-dev v6b)'
    if min(info.get('w', 0), info.get('h', 0)) < MIN_SIDE:
        return False, 'short side < 32 px (possible lookup table)'
    if info.get('fmt', '?') == '?':
        return False, 'unknown format'
    if n.startswith(ALWAYS_SKIP):
        return False, 'placeholder'
    if info.get('group') == 'stream' and n.startswith(STREAM_SKIP):
        return False, 'left to the game (shadow, effect, font or minimap)'
    return True, ''


def method_for(info):
    """'lanczos' or 'model' for a texture (info: name, cls, group)."""
    n = info.get('name', '').lstrip('_')
    if info.get('group') == 'cars':
        return 'lanczos'   # stage (c): every car texture
    if n.startswith(LANCZOS_PREFIXES):
        return 'lanczos'
    if n.startswith(CAR_PREFIXES) or info.get('cls') == CAR_CLASS:
        return 'lanczos'
    return 'model'


def alpha_class(a):
    u = np.unique(a)
    if len(u) == 1 and u[0] == 255:
        return 'none'
    if set(u.tolist()) <= {0, 255}:
        return 'cutout'
    return 'mask'


def alpha_class_for(a, au):
    """The game's AlphaUsage wins (0/1/2 -> none/cutout/mask); a cutout without one transparent texel is opaque."""
    cls = {0: 'none', 1: 'cutout', 2: 'mask'}.get(au) if au is not None else None
    if cls is None:
        cls = alpha_class(a)
    if cls == 'cutout' and alpha_class(a) == 'none':
        cls = 'none'
    return cls


def is_normal_map(rgb):
    m = rgb.reshape(-1, 3).mean(0)
    return bool(m[2] > 180 and 100 < m[0] < 160 and 100 < m[1] < 160)


def pad_modes(info):
    """numpy pad modes for (V axis, U axis) from the tiling bits (bit 1 = V wraps, bit 0 = U wraps)."""
    t = info.get('tile')
    if t is None:
        return 'wrap', 'wrap'
    return ('wrap' if t & 2 else 'edge'), ('wrap' if t & 1 else 'edge')


def pad2(img, pad, modes):
    out = np.pad(img, ((pad, pad), (0, 0)) + ((0, 0),) * (img.ndim - 2), mode=modes[0])
    return np.pad(out, ((0, 0), (pad, pad)) + ((0, 0),) * (img.ndim - 2), mode=modes[1])


def scale_for(w, h):
    """4x under 1024 px on the longer side, else 2x; halved until the longer side is at most 4096 (D-122)."""
    scale = 4 if max(w, h) < 1024 else 2
    while max(w, h) * scale > CAP and scale > 1:
        scale //= 2
    return scale


def lanczos4x(rgb):
    h, w = rgb.shape[:2]
    return np.asarray(Image.fromarray(rgb).resize((w * 4, h * 4), Image.LANCZOS)
                      .filter(ImageFilter.UnsharpMask(radius=2, percent=40, threshold=2)))


def mips(rgb, a, cls):
    levels = [(rgb, a)]
    h, w = rgb.shape[:2]
    while w > 1 or h > 1:
        w, h = max(w // 2, 1), max(h // 2, 1)
        r = np.asarray(Image.fromarray(levels[0][0]).resize((w, h), Image.BOX))
        al = np.asarray(Image.fromarray(levels[0][1]).resize((w, h), Image.BOX))
        if cls == 'cutout':
            al = np.where(al >= 128, 255, 0).astype(np.uint8)
        levels.append((r, al))
    return levels


def encode(levels, fmt):
    """Each level encoded in fmt (etcpak for BC1/BC7; raw for rgba8)."""
    out = []
    for r, a in levels:
        h, w = r.shape[:2]
        rgba = np.ascontiguousarray(np.dstack([r, a]))
        if fmt == 'rgba8':
            out.append(rgba.tobytes())
            continue
        import etcpak
        if w % 4 or h % 4:   # the encoders work on whole 4x4 blocks: pad by edge replication
            rgba = np.ascontiguousarray(np.pad(rgba, ((0, (-h) % 4), (0, (-w) % 4), (0, 0)), mode='edge'))
        hh, ww = rgba.shape[:2]
        out.append((etcpak.compress_bc7 if fmt == 'bc7' else etcpak.compress_bc1)(rgba.tobytes(), ww, hh))
    return out


def luma(rgb):
    return rgb.astype(np.float64) @ np.array([0.299, 0.587, 0.114])


def hf(rgb):
    """Mean |luma - Gaussian blur 2| (run 26 audit)."""
    im = Image.fromarray(np.ascontiguousarray(rgb)).convert('L')
    a = np.asarray(im, np.float32)
    b = np.asarray(im.filter(ImageFilter.GaussianBlur(2)), np.float32)
    return float(np.abs(a - b).mean())


def block_score(rgb):
    """Gradient on 16 px block edges / off them (run 25 pick_v6): a DXT-block mosaic scores high."""
    gy, gx = np.gradient(luma(rgb))
    g = np.hypot(gx, gy)
    h, w = g.shape
    xs = np.zeros(w, bool); xs[15::16] = True; xs[16::16] = True
    ys = np.zeros(h, bool); ys[15::16] = True; ys[16::16] = True
    if not xs.any() or not ys.any() or xs.all() or ys.all():
        return 1.0
    on = g[:, xs].mean() + g[ys, :].mean()
    off = g[:, ~xs].mean() + g[~ys, :].mean()
    return float(on / max(off, 1e-6))


# --- the model path (torch and spandrel are imported here only) ---

def device():
    import torch
    return 'mps' if torch.backends.mps.is_available() else 'cpu'


def load_model(path):
    import spandrel
    m = spandrel.ModelLoader().load_from_file(str(path))
    m.to(device()).eval()
    return m


def upscale_rgb(model, rgb, tile=TILE):
    """rgb: HxWx3 uint8 -> (4H)x(4W)x3 uint8, tiled with overlap to bound GPU memory."""
    import torch
    dev = device()
    with torch.inference_mode():
        h, w = rgb.shape[:2]
        x = torch.from_numpy(np.ascontiguousarray(rgb)).permute(2, 0, 1).float().div(255).unsqueeze(0)
        out = torch.zeros(1, 3, h * 4, w * 4)
        for y0 in range(0, h, tile):
            for x0 in range(0, w, tile):
                ya, xa = max(y0 - OVERLAP, 0), max(x0 - OVERLAP, 0)
                yb, xb = min(y0 + tile + OVERLAP, h), min(x0 + tile + OVERLAP, w)
                t = model(x[:, :, ya:yb, xa:xb].to(dev)).cpu()
                oy, ox = (y0 - ya) * 4, (x0 - xa) * 4
                th, tw = min(tile, h - y0) * 4, min(tile, w - x0) * 4
                out[:, :, y0 * 4:y0 * 4 + th, x0 * 4:x0 * 4 + tw] = t[:, :, oy:oy + th, ox:ox + tw]
        return out.clamp(0, 1).mul(255).round().byte()[0].permute(1, 2, 0).numpy()


def model_upscaler(path):
    """A function padded rgb -> 4x rgb running the model; one retry with 128 px tiles when the GPU runs out of
    memory (8 GB Macs)."""
    model = load_model(path)

    def run(rgb):
        try:
            return upscale_rgb(model, rgb, TILE)
        except RuntimeError as e:
            if 'out of memory' not in str(e).lower():
                raise
            import torch
            if hasattr(torch, 'mps') and hasattr(torch.mps, 'empty_cache'):
                torch.mps.empty_cache()
            return upscale_rgb(model, rgb, SMALL_TILE)
    return run


# --- one texture ---

def process_one(key, info, src, outdir, model_fn=None):
    """Upscale one texture (src: its top level as a PNG) into outdir/<key>.dds. Returns the manifest record; writes
    nothing for a skipped texture. model_fn: padded rgb -> 4x rgb (None: model-path textures raise)."""
    t0 = time.time()
    im = np.asarray(Image.open(src).convert('RGBA'))
    rgb, a = np.ascontiguousarray(im[..., :3]), np.ascontiguousarray(im[..., 3])
    h, w = rgb.shape[:2]
    rec = {'key': key, 'name': info.get('name', ''), 'source_sha256': sha(src), 'source_size': [w, h]}
    if min(w, h) < MIN_SIDE:
        rec['skipped'] = 'short side < 32 px (possible lookup table)'
        return rec
    normal = is_normal_map(rgb)
    cls = alpha_class_for(a, info.get('au'))
    method = method_for(info)
    modes = pad_modes(info)
    if method == 'model':
        if model_fn is None:
            raise RuntimeError('the upscaling model is not loaded')
        up = model_fn(pad2(rgb, PAD, modes))[PAD * 4:PAD * 4 + h * 4, PAD * 4:PAD * 4 + w * 4]
        up = np.ascontiguousarray(up)
        rec['mosaic'] = {'hf_ratio': round(hf(up) / max(hf(np.asarray(Image.fromarray(rgb).resize(
            (w * 4, h * 4), Image.LANCZOS))), 1e-6), 3), 'block': round(block_score(up), 3)}
    else:
        up = lanczos4x(rgb)
    t_up = time.time() - t0
    scale = scale_for(w, h)
    W, H = w * scale, h * scale
    if scale != 4:
        up = np.asarray(Image.fromarray(up).resize((W, H), Image.LANCZOS))
    if normal:   # back to unit vectors: the colour model does not know the texels are directions
        n = up.astype(np.float32) / 127.5 - 1.0
        n /= np.maximum(np.linalg.norm(n, axis=2, keepdims=True), 1e-6)
        up = np.clip((n + 1.0) * 127.5 + 0.5, 0, 255).astype(np.uint8)
    if cls != 'none':
        al = np.asarray(Image.fromarray(a).resize((W, H), Image.LANCZOS))
    else:
        al = np.full((H, W), 255, np.uint8)
    if cls == 'cutout':
        al = np.where(al >= 128, 255, 0).astype(np.uint8)
    fmt = 'bc1' if cls == 'none' and not normal else 'bc7'
    levels = mips(np.ascontiguousarray(up), np.ascontiguousarray(al), cls)
    enc = encode(levels, fmt)
    out = Path(outdir) / f'{key}.dds'
    dds.write(out, fmt, W, H, enc)
    rec.update(method=method, pad=list(modes), normal_map=normal, alpha=cls, scale=scale, size=[W, H], format=fmt,
               mips=len(levels), output_sha256=sha(out), upscale_s=round(t_up, 2), total_s=round(time.time() - t0, 2),
               bytes=out.stat().st_size)
    return rec
