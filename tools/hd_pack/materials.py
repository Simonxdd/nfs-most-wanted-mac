#!/usr/bin/env python3
"""Materials (3.0): the ORM maps (cavity, roughness, metal) for the game's "Material sets" and "Wall sheen" settings,
made on this Mac from the HD texture pack's albedo. Runs inside Setup's "Make HD textures" step, after hdpack.py.

    materials.py --hd-cache DIR --out DIR [--model-cache DIR] [--model-zip PATH] [--device mps|cpu]

For each texture of materials-keys.txt (our own list: key, family, roughness prior, metal, glint) whose albedo is in
the HD pack (<hd-cache>/pack, 4x the game):
  1. box-down the albedo to half its side (2x the game: the game's size check passes);
  2. 1x-PBRify_RoughnessV2 (CC0, by Kim2091) on the wrap-padded RGB, tiled 512 / overlap 32 (spandrel; MPS, else CPU);
  3. r = clamp(prior + (R2 - mean R2), prior - 0.25, prior + 0.25), clamped to [0, 1];
  4. mips r(n+1) = sqrt(box2(r(n)^2)) (the mean GGX alpha = r^2 is kept per level);
  5. R = cavity (luma band-pass, mean occlusion 0.07, floor 0.60; box mips), G = r, B = 255 on the metal keys else 0,
     A = 255; BC7 (etcpak), DX10 UNORM, full mip chain.
Writes <out>/<key>.dds, glint.tsv ("key, family, metal" for the wall sheen), manifest.json, and index.tsv
("KEY orm FILE") last. Resumes: a key already made (manifest.jsonl) is not made again.
Model: the pinned PBRify_Remix-1.7.2.zip (SHA-256) is downloaded once; its RoughnessV2 .pth is read with
torch.load(weights_only=True) (tensors only, no pickled code), saved as safetensors (pinned SHA-256) in the model
folder and the zip deleted. Progress lines start with "HD textures:". Exit 0 done, 2 skipped (no HD pack yet), 1 failed.
"""
import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hdpack  # noqa: E402  (stdlib only at import)

PREFIX = hdpack.PREFIX
KEYS_FILE = HERE / 'materials-keys.txt'
RULES = 'orm-rules-v3'
REEXEC_ENV = 'NFSMW_HD_REEXEC'
# knowledge INTAKE I3: the release zip, the .pth inside it, and the safetensors it converts to (tensor-equal, no metadata).
ZIP = {
    'name': 'PBRify_Remix-1.7.2.zip',
    'url': 'https://github.com/Kim2091/PBRify_Remix/releases/download/v1.7.2/PBRify_Remix-1.7.2.zip',
    'sha256': 'd787a88f644c542620f869e0c61e71f795488020fcece9a97e774ca2c2cecb0b',
    'mb': 117,
}
PTH = {'member': 'PBRify_Remix-1.7.2/Models/1x-PBRify_RoughnessV2.pth',
       'sha256': '7003c39041af64cdb77d5120bd560a2878538fbb14a07657d8fd12aac5773679'}
MODEL = {'name': '1x-PBRify_RoughnessV2.safetensors',
         'sha256': 'ef8c91cd15724baf13cf95bd756649d4f4267b70899433de6ca76049da46ed9d',
         'licence': 'CC0 1.0 (Kim2091, PBRify)'}
SPAN = 0.25
TILE, OVER, PAD = 512, 32, 16
CAV_MEAN, CAV_FLOOR, CAV_S1, CAV_S2 = 0.07, 0.60, 0.7, 3.0
GLINT_FAMILIES = ('concrete', 'brick', 'stone', 'cobble', 'metal')
FAMILIES = ('road', 'concrete', 'brick', 'cobble', 'stone', 'gravel', 'dirt', 'sand', 'metal')
PROGRESS_EVERY, PROGRESS_SECONDS = 25, 30


def say(msg):
    print(PREFIX + msg, flush=True)


# --- shared with relief.py: plain Python, no packages ---

def read_keys(path, cols):
    """The shipped list: whitespace columns, '#' comments. Returns a list of dicts."""
    rows = []
    for line in Path(path).read_text().splitlines():
        if line.strip() and not line.startswith('#'):
            rows.append(dict(zip(cols, line.split())))
    return rows


def pack_dir(hd):
    """The HD pack's folder (index.tsv present: the pack is finished), or None. <hd>/pack, or <hd> itself."""
    for d in (hd / 'pack', hd):
        if (d / 'index.tsv').is_file():
            return d
    return None


def albedo_files(pack):
    out = {}
    for line in (pack / 'index.tsv').read_text().splitlines():
        p = line.split('\t')
        if len(p) == 3 and p[1] == 'albedo' and not line.startswith('#'):
            out[p[0].lower()] = pack / p[2]
    return out


def names_of(hd):
    """Texture names from the player's own game (the HD step's list.tsv), for the manifests; {} without one."""
    try:
        return {r['key'].lower(): r['name'] for r in hdpack.read_list(hd)}
    except Exception:
        return {}


def hd_python(hd):
    """The HD step's private environment (made by hdpack.tools), or None when it is not ready."""
    py, stamp = hd / 'venv/bin/python', hd / 'venv/.ready'
    try:
        if py.exists() and stamp.read_text() == (HERE / 'requirements.txt').read_text():
            return py
    except OSError:
        pass
    return None


def ensure_env(hd, modules, what):
    """Returns None to run here (the modules import), else re-runs this script in the HD step's environment and
    returns its exit code (2 with one 'skipped' line when that environment is not there)."""
    import importlib.util
    if all(importlib.util.find_spec(m) for m in modules):
        return None
    py = hd_python(hd)
    if py is None or os.environ.get(REEXEC_ENV):
        say(f'{what} skipped (the HD textures\' tools are not installed yet)')
        return 2
    env = dict(os.environ, **{REEXEC_ENV: '1'})
    return subprocess.run([str(py), '-I', str(Path(sys.argv[0]).resolve())] + sys.argv[1:], env=env).returncode


def write_index(out, header, rows):
    """index.tsv last, atomically (the packager and the game take the folder only once it is there)."""
    tmp = out / '.index.tsv.part'
    tmp.write_text(header + '\n' + ''.join(r + '\n' for r in rows))
    os.replace(tmp, out / 'index.tsv')


# --- the model ---

def fetch_model(model_dir, zip_path=None):
    """The RoughnessV2 safetensors in model_dir: kept when its hash matches, else made from the pinned zip."""
    model_dir.mkdir(parents=True, exist_ok=True)
    dst = model_dir / MODEL['name']
    if dst.is_file() and hdpack.sha(dst) == MODEL['sha256']:
        return dst
    z, fetched = (Path(zip_path) if zip_path else model_dir / ZIP['name']), False
    if not (z.is_file() and hdpack.sha(z) == ZIP['sha256']):
        if zip_path:
            raise ValueError(f'{z.name} does not match its pinned SHA-256')
        say(f"downloading the material model ({ZIP['mb']} MB, {MODEL['licence']})")
        hdpack.download(ZIP['url'], z, ZIP['sha256'])
        fetched = True
    with zipfile.ZipFile(z) as zf:
        data = zf.read(PTH['member'])
    if hashlib.sha256(data).hexdigest() != PTH['sha256']:
        raise ValueError('the model inside the zip does not match its pinned SHA-256')
    convert(data, dst)
    if fetched:
        z.unlink()
    return dst


def convert(pth_bytes, dst):
    """.pth bytes -> safetensors at dst: torch.load(weights_only=True) (tensors and plain containers only)."""
    import torch
    from safetensors.torch import save_file
    sd = torch.load(io.BytesIO(pth_bytes), map_location='cpu', weights_only=True)
    if isinstance(sd, dict) and len(sd) == 1 and isinstance(next(iter(sd.values())), dict):
        sd = next(iter(sd.values()))   # {'params': {...}}
    if not sd or not all(isinstance(v, torch.Tensor) for v in sd.values()):
        raise ValueError('the model file holds something other than tensors')
    tmp = dst.with_name('.' + dst.name + '.part')
    save_file({k: v.contiguous() for k, v in sd.items()}, str(tmp))
    if hdpack.sha(tmp) != MODEL['sha256']:
        tmp.unlink()
        raise ValueError('the converted model does not match its pinned SHA-256')
    os.replace(tmp, dst)


def device():
    import torch
    return 'mps' if torch.backends.mps.is_available() else 'cpu'


def load_model(path, dev):
    import spandrel
    m = spandrel.ModelLoader().load_from_file(str(path))
    m.to(dev).eval()
    return m


def roughness(model, rgb, dev):
    """rgb HxWx3 uint8 -> HxW float32 in [0, 1] (the mean of the model's three outputs), wrap-padded, tiled."""
    import numpy as np
    import torch
    h, w = rgb.shape[:2]
    p = min(PAD, h, w)
    x = np.pad(rgb, ((p, p), (p, p), (0, 0)), mode='wrap')
    t = torch.from_numpy(np.ascontiguousarray(x)).permute(2, 0, 1).float().div(255).unsqueeze(0)
    H, W = t.shape[2:]
    out = torch.zeros(1, 3, H, W)
    with torch.inference_mode():
        step = TILE - 2 * OVER
        for y in range(0, H, step):
            for xx in range(0, W, step):
                ya, xa = max(y - OVER, 0), max(xx - OVER, 0)
                yb, xb = min(y + step + OVER, H), min(xx + step + OVER, W)
                o = model(t[:, :, ya:yb, xa:xb].to(dev)).float().cpu()
                cy, cx = y - ya, xx - xa
                ey, ex = min(step, H - y), min(step, W - xx)
                out[:, :, y:y + ey, xx:xx + ex] = o[:, :, cy:cy + ey, cx:cx + ex]
    r = out[0].mean(0).clamp(0, 1).numpy()
    return r[p:p + h, p:p + w].astype(np.float32)


# --- the recipe (numpy) ---

def down2(a):
    h, w = a.shape
    fy, fx = (2 if h > 1 else 1), (2 if w > 1 else 1)
    return a[:h // fy * fy, :w // fx * fx].reshape(h // fy, fy, w // fx, fx).mean((1, 3))


def chain(r):
    """Roughness mips: sqrt of the box mean of r^2, down to 1x1."""
    import numpy as np
    lv = [r]
    while lv[-1].shape != (1, 1):
        lv.append(np.sqrt(down2(lv[-1] ** 2)))
    return lv


def chain_mean(c):
    lv = [c]
    while lv[-1].shape != (1, 1):
        lv.append(down2(lv[-1]))
    return lv


def gauss_wrap(a, sigma):
    import numpy as np
    h, w = a.shape
    fy, fx = np.fft.fftfreq(h)[:, None], np.fft.fftfreq(w)[None, :]
    k = np.exp(-2.0 * (np.pi * sigma) ** 2 * (fy ** 2 + fx ** 2))
    return np.real(np.fft.ifft2(np.fft.fft2(a) * k))


def cavity(rgb, target=CAV_MEAN):
    """rgb HxWx3 uint8 (sRGB) -> (HxW float32 in [CAV_FLOOR, 1], g); mean(1 - R) = target by bisection on g."""
    import numpy as np
    c = rgb.astype(np.float64) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    L = lin @ np.array([0.2126, 0.7152, 0.0722])
    B = gauss_wrap(L, CAV_S1) - gauss_wrap(L, CAV_S2)
    D = np.maximum(0.0, -B) / (B.std() + 1e-4)

    def occ(g):
        return float((1.0 - np.clip(1.0 - g * D, CAV_FLOOR, 1.0)).mean())
    lo, hi = 0.0, 1.0
    while occ(hi) < target and hi < 1e6:
        hi *= 2.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if occ(mid) < target else (lo, mid)
    g = 0.5 * (lo + hi)
    return np.clip(1.0 - g * D, CAV_FLOOR, 1.0).astype(np.float32), g


def orm_levels(r, cav, metal):
    """(rgb, a) uint8 levels for upscale.encode: R cavity, G roughness, B metal, A 255."""
    import numpy as np
    out = []
    for lr, lc in zip(chain(r.astype(np.float32)), chain_mean(cav)):
        h, w = lr.shape
        rgb = np.empty((h, w, 3), np.uint8)
        rgb[..., 0] = np.clip(np.round(lc * 255), 0, 255)
        rgb[..., 1] = np.clip(np.round(lr * 255), 0, 255)
        rgb[..., 2] = 255 if metal else 0
        out.append((rgb, np.full((h, w), 255, np.uint8)))
    return out


def make_one(row, src, out, rough_fn):
    """One key: albedo DDS -> <out>/<key>.dds. Returns its manifest record."""
    import numpy as np
    from PIL import Image
    import dds
    import upscale
    t0 = time.time()
    with Image.open(src) as im:
        im.load()
        rgb = np.asarray(im.convert('RGB'))
    H, W = rgb.shape[:2]
    w, h = max(W // 2, 1), max(H // 2, 1)
    small = np.asarray(Image.fromarray(rgb).resize((w, h), Image.BOX))
    pr, metal = float(row['prior']), row['metal'] == '1'
    r2 = rough_fn(small)
    r2m = float(r2.mean())
    r = np.clip(pr + (r2 - r2m), pr - SPAN, pr + SPAN).clip(0, 1).astype(np.float32)
    cav, g = cavity(small)
    levels = orm_levels(r, cav, metal)
    dds.write(out / f"{row['key']}.dds", 'bc7', w, h, upscale.encode(levels, 'bc7'))
    return {'key': row['key'], 'family': row['family'], 'prior': pr, 'metal': int(metal),
            'mean': float(r.mean()), 'p5': float(np.percentile(r, 5)), 'p95': float(np.percentile(r, 95)),
            'r2_mean': r2m, 'cav_mean': float((1.0 - cav).mean()), 'cav_min': float(cav.min()), 'cav_g': g,
            'size': [w, h], 'source': [W, H], 'levels': len(levels), 'sec': round(time.time() - t0, 2)}


def read_jsonl(p):
    out = {}
    if p.is_file():
        for line in p.read_text().splitlines():
            try:
                rec = json.loads(line)
                out[rec['key']] = rec
            except (ValueError, KeyError):
                continue
    return out


def run(hd, out, rough_fn=None, model_path=None, dev=None, keys_file=KEYS_FILE):
    """Makes (or finishes) the pack in out. rough_fn (tests) replaces the model. Returns the exit code."""
    pack = pack_dir(hd)
    if pack is None:
        say('materials skipped (the HD pack is not finished)')
        return 2
    rows = read_keys(keys_file, ('key', 'family', 'prior', 'metal', 'glint'))
    files = albedo_files(pack)
    out.mkdir(parents=True, exist_ok=True)
    jl = out / 'manifest.jsonl'
    done = read_jsonl(jl)
    have = [r for r in rows if r['key'] in files]
    missing = len(rows) - len(have)

    def current(r):
        d = done.get(r['key'])
        return d and d['prior'] == float(r['prior']) and d['metal'] == int(r['metal']) and \
            (out / f"{r['key']}.dds").is_file()
    work = [r for r in have if not current(r)]
    say(f'materials: {len(have)} textures ({len(have) - len(work)} already made'
        f'{f", {missing} not in the HD pack" if missing else ""})')
    if work and rough_fn is None:
        dev = dev or device()
        model = load_model(model_path, dev)

        def rough_fn(rgb):
            nonlocal dev, model
            try:
                return roughness(model, rgb, dev)
            except RuntimeError:
                if dev == 'cpu':
                    raise
                dev = 'cpu'
                model = load_model(model_path, dev)
                say('materials: the GPU path failed; carrying on with the CPU')
                return roughness(model, rgb, dev)
    t_all, t_last, fails = time.time(), time.time(), 0
    for i, r in enumerate(work):
        try:
            rec = make_one(r, files[r['key']], out, rough_fn)
        except Exception as e:
            fails += 1
            say(f"materials: {r['key']} not made ({str(e)[:80]})")
            continue
        with open(jl, 'a') as f:
            f.write(json.dumps(rec) + '\n')
        done[r['key']] = rec
        n = i + 1
        if n % PROGRESS_EVERY == 0 or time.time() - t_last > PROGRESS_SECONDS:
            t_last = time.time()
            left = (time.time() - t_all) / n * (len(work) - n)
            say(f'materials: {n} of {len(work)} (about {int(left // 60) + 1} min left)')
    made = [r for r in rows if current(r)]
    if not made:
        say('materials: stopped (no ORM map made)')
        return 1
    man = {'rules': RULES, 'model': MODEL['name'], 'model_sha256': MODEL['sha256'], 'span': SPAN,
           'cav_mean_target': CAV_MEAN, 'cav_sigmas': [CAV_S1, CAV_S2], 'cav_floor': CAV_FLOOR,
           'seconds': round(sum(done[r['key']]['sec'] for r in made), 1), 'keys': {r['key']: done[r['key']] for r in made}}
    (out / 'manifest.json').write_text(json.dumps(man, indent=1))
    glint = sorted((r['key'], r['family'], r['metal']) for r in made if r['glint'] == '1')
    (out / 'glint.tsv').write_text(f'# Materials facade glint ({RULES}): key, family, metal\n' +
                                   ''.join(f'{k}\t{f}\t{m}\n' for k, f, m in glint))
    write_index(out, f'# Materials ({RULES}, 1x-PBRify_RoughnessV2, BC7 DX10): key, slot, file',
                [f"{r['key']}\torm\t{r['key']}.dds" for r in made])
    say(f'materials ready ({len(made)} ORM maps, {len(glint)} glint rows'
        f'{f", {fails} failed" if fails else ""}; {time.time() - t_all:.0f} s)')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--hd-cache', type=Path, help='the HD step\'s folder (default as hdpack.py)')
    ap.add_argument('--out', type=Path, required=True, help='the Materials folder to make')
    ap.add_argument('--model-cache', type=Path, help='where the model is kept (default <hd-cache>/model)')
    ap.add_argument('--model-zip', type=Path, help='a local copy of the pinned PBRify_Remix zip (no download)')
    ap.add_argument('--device', choices=('mps', 'cpu'))
    a = ap.parse_args(argv)
    hd = hdpack.cache_root(a.hd_cache)
    if pack_dir(hd) is None:
        say('materials skipped (the HD pack is not finished)')
        return 2
    rc = ensure_env(hd, ('numpy', 'PIL', 'torch', 'spandrel', 'safetensors', 'etcpak'), 'materials')
    if rc is not None:
        return rc
    try:
        model = fetch_model(a.model_cache or hd / 'model', a.model_zip)
        return run(hd, a.out.expanduser(), model_path=model, dev=a.device)
    except Exception as e:   # never a traceback in the player's setup window
        say(f'materials stopped: {str(e)[:160]}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
