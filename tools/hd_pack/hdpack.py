#!/usr/bin/env python3
"""The optional HD texture pack (2.1): made on this Mac from your own game files, never downloaded.

    hdpack.py --game DIR [--stuff DIR] [--cache DIR] [--limit N]     make (or finish) the pack
    hdpack.py --check [--cache DIR]                                  desk checks of a finished pack (seconds)

Run by the setup (step 6, "Make HD textures") before packaging; it can also run on its own. Every stage resumes
where it stopped: run it (or the setup) again and it carries on.
  1. tools: a private Python environment for the upscaler (PyTorch, spandrel, etcpak; pinned, about 1 GB, once);
  2. model: 4x-PBRify-UpscalerV4 (CC0, by Kim2091), pinned SHA-256; without it nothing is packaged;
  3. photos: ambientCG CC0 photo materials for the fine grain of roads, rock, grass, metal, wood (pinned SHA-256;
     one that cannot be fetched is left out and those textures keep the plain upscale);
  4. list: every texture of your game's GLOBAL, TRACKS, CARS and FRONTEND files (and the Xbox 360 Stuff Pack's
     TRACKS/TexWizardX360/textures.bin when you have it), each top level saved as a PNG in the cache;
  5. upscale: each texture by upscale.py's recipe (the model on the GPU, or Lanczos); the long part;
  6. grain: hero.py's photo grain on the matching world textures;
  7. pack: index.tsv beside the DDS files in the cache; the packager copies it into the app (Contents/Resources/
     RemasterPack), where the game's "Remaster HD textures" setting finds it.
Progress lines start with "HD textures:". Nothing in the game folder is changed. The cache defaults to
~/Library/NFSMW-Native-Setup/hd (the environment variable NFSMW_HD_CACHE overrides it)."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CACHE_ENV = 'NFSMW_HD_CACHE'
PREFIX = 'HD textures: '
# Pinned downloads (knowledge INTAKE I3 and I5; the same hashes the owner's pack was made with).
MODEL = {
    'name': '4x-PBRify_UpscalerV4.safetensors',
    'url': 'https://github.com/Kim2091/Kim2091-Models/releases/download/4x-PBRify_UpscalerV4/'
           '4x-PBRify_UpscalerV4.safetensors',
    'sha256': 'a00fa50116895a9b766aeb012edad0a14d66f3afda65539efc0f6fc28c991ba2',
    'licence': 'CC0 1.0 (Kim2091, PBRify)',
    'mb': 140,
}
PHOTO_URL = 'https://ambientcg.com/get?file={}_2K-JPG.zip'
# SHA-256 of each ambientCG 2K-JPG zip (CC0 1.0); only the *_Color.jpg inside is used.
PHOTOS = {
    'Asphalt031': 'f2a02ea9ea1fd97d2a6d27f46ebac069a5851e3ae7c73fa991abf563b97786c7',
    'Bark014': '2ae21b265b55512521b0c1f161ebaa5bbc9867d8e47327974027d38fb711d7eb',
    'Grass004': 'd7d3f08e8bb1e59aed0c3dbb85ea0b46d5308481f89b6a3fd62c0ec9e862245b',
    'Gravel043': 'd520aa16697e6b24fed4a6529d51df93970a36fcfb30be5e1171d681b8303665',
    'Ground054': '643e7c8689bac041f17c660eff6a5459bfe0535d4466737beb0e64902b23235a',
    'Ground110': '61b550147e82350ec12f3f1d92e9ccd3ae6b6bfc8b3f8991cd80b54524c2a515',
    'Metal063': '4c4a995457002ff5288c337254893d3bd42bdd13cecba3e3dadf84e71ebae4a6',
    'Rock051': '3494755ee283b120a62e1adf1c46b00aad1436ed21c48882dffb9654e7830d64',
    'Wood051': 'ef50cd051a1d998bf2a03e243e991474415d948af6d4b76d291c937654c97d4b',
}
# Q5 (owner default): the Stuff Pack's textures are upscaled whenever the setup finds them.
UPSCALE_STUFF_PACK = True
FREE_GB = 10            # the venv, PNGs, pack and the app's copy: stop early instead of filling the disk
# --check: cutouts are written 0/255 at every mip; BC7 (etcpak) may round them by a few levels (1 seen for 0).
CUTOUT_SLACK = 16
EXPECTED_SHARE = 0.95   # --check: index rows / textures selected from the files found
PROGRESS_EVERY, PROGRESS_SECONDS = 25, 30
INDEX_HEADER = ('# Remaster HD texture pack, made on this Mac from your game (4x-PBRify-UpscalerV4 or Lanczos; '
                'ambientCG CC0 grain; BC7/BC1, DX10)')
LIST_COLUMNS = ('key', 'name', 'w', 'h', 'fmt', 'mips', 'au', 'tile', 'cls', 'group', 'file', 'selected', 'why')


def say(msg):
    print(PREFIX + msg, flush=True)


def cache_root(arg=None):
    if arg:
        return Path(arg).expanduser()
    if os.environ.get(CACHE_ENV):
        return Path(os.environ[CACHE_ENV]).expanduser()
    return Path.home() / 'Library/NFSMW-Native-Setup/hd'


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# --- the driver's part: plain Python, no packages ---

def tools(cache):
    """The upscaler's environment in the cache: pinned ready-made wheels only (as setup.sh installs its own)."""
    venv = cache / 'venv'
    py = venv / 'bin/python'
    stamp = venv / '.ready'
    req = (HERE / 'requirements.txt').read_text()
    if py.exists() and stamp.exists() and stamp.read_text() == req:
        return py
    say("installing the upscaler's tools (PyTorch and friends; about 1 GB, once)")
    if not py.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
    with open(cache / 'pip.log', 'w') as log:
        r = subprocess.run([str(py), '-m', 'pip', 'install', '--disable-pip-version-check', '--retries', '8',
                            '--timeout', '30', '--only-binary=:all:', '-r', str(HERE / 'requirements.txt')],
                           stdout=log, stderr=subprocess.STDOUT)
    if r.returncode:
        raise SystemExit(f"the upscaler's tools could not be installed (see {cache / 'pip.log'})")
    stamp.write_text(req)
    return py


def download(url, dst, want):
    """url -> dst when its SHA-256 is `want`; the partial file is hidden and removed on any mismatch."""
    tmp = dst.with_name('.' + dst.name + '.part')
    req = urllib.request.Request(url, headers={'User-Agent': 'nfsmw-native-mac setup'})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, 'wb') as f:
        shutil.copyfileobj(r, f)
    if sha(tmp) != want:
        tmp.unlink()
        raise ValueError('did not match its pinned SHA-256; not used')
    os.replace(tmp, dst)
    return dst


def fetch_model(cache):
    d = cache / 'model'
    d.mkdir(parents=True, exist_ok=True)
    dst = d / MODEL['name']
    if dst.is_file() and sha(dst) == MODEL['sha256']:
        return dst
    say(f"downloading the upscaler ({MODEL['mb']} MB, {MODEL['licence']})")
    try:
        return download(MODEL['url'], dst, MODEL['sha256'])
    except Exception as e:
        raise SystemExit(f'the upscaling model could not be downloaded ({str(e)[:100]})')


def fetch_photos(cache, materials=None):
    """The grain photos: each pinned zip fetched once, its *_Color.jpg kept. Never fails: a missing photo only leaves
    its textures without grain."""
    out = cache / 'photos'
    out.mkdir(parents=True, exist_ok=True)
    mats = sorted(materials or PHOTOS)
    todo = [m for m in mats if not (out / f'{m}_2K-JPG_Color.jpg').is_file()]
    if todo:
        say(f'downloading the grain photos ({len(todo)} from ambientCG, CC0)')
    for mat in todo:
        want = PHOTOS.get(mat)
        if not want or len(want) != 64:
            say(f'  {mat}: no pinned hash; left out')
            continue
        z = out / f'{mat}.zip'
        try:
            download(PHOTO_URL.format(mat), z, want)
            with zipfile.ZipFile(z) as zf:
                name = next(n for n in zf.namelist() if n.endswith('_Color.jpg'))
                tmp = out / f'.{mat}.jpg.part'
                tmp.write_bytes(zf.read(name))
                os.replace(tmp, out / f'{mat}_2K-JPG_Color.jpg')
        except Exception as e:
            say(f'  {mat}: not downloaded ({str(e)[:80]}); left out')
        finally:
            if z.exists():
                z.unlink()
    got = sum((out / f'{m}_2K-JPG_Color.jpg').is_file() for m in mats)
    say(f'{got} of {len(mats)} grain photos ready')
    return out


def resolve_ci(base, rel):
    p = Path(base)
    for part in rel.split('/'):
        if not p.is_dir():
            return None
        p = next((c for c in sorted(p.iterdir()) if c.name.lower() == part.lower()), None)
        if p is None:
            return None
    return p


def inputs(game, stuff=()):
    """[(absolute path, path relative to the game folder)] in reading order: GLOBAL, TRACKS (streamed world and
    minimap), CARS, FRONTEND, then the Stuff Pack's textures.bin (the game folder's, else a Stuff Pack folder's)."""
    game = Path(game)
    out = []

    def files(rel_dir, exts, sub=None):
        d = resolve_ci(game, rel_dir)
        if d is None or not d.is_dir():
            return
        for p in sorted(d.iterdir(), key=lambda q: q.name.lower()):
            if sub is not None:
                if p.is_dir():
                    t = resolve_ci(p, sub)
                    if t is not None and t.is_file():
                        out.append((t, f'{rel_dir}/{p.name}/{t.name}'))
            elif p.is_file() and p.suffix.lower() in exts and not p.name.startswith('.'):
                out.append((p, f'{rel_dir}/{p.name}'))
    files('GLOBAL', ('.bin', '.bun', '.lzc'))
    files('TRACKS', ('.bun',))
    files('TRACKS/L2RA', ('.bin',))
    files('CARS', (), sub='TEXTURES.BIN')
    files('FRONTEND', ('.bun', '.lzc'))
    if UPSCALE_STUFF_PACK:
        cands = [game]
        try:
            sys.path.insert(0, str(ROOT / 'tools/setup_kit'))
            import x360_stuff
            for d in list(stuff) + [game]:
                cands += x360_stuff.roots(d)
        except Exception:
            cands += [Path(d) for d in stuff]
        for c in cands:
            t = resolve_ci(c, 'TRACKS/TexWizardX360/textures.bin')
            if t is not None and t.is_file():
                out.append((t, 'TRACKS/TexWizardX360/textures.bin'))
                break
    return out


def run_worker(py, args, cache, model):
    cmd = [str(py), str(HERE / 'hdpack.py'), '--worker', '--game', str(args.game), '--cache', str(cache),
           '--model', str(model)]
    for s in args.stuff:
        cmd += ['--stuff', str(s)]
    if args.limit:
        cmd += ['--limit', str(args.limit)]
    return subprocess.run(cmd).returncode


# --- the worker's part: runs in the upscaler's environment (numpy, Pillow, etcpak; torch only in the model path) ---

def read_list(cache):
    p = cache / 'list.tsv'
    if not p.is_file():
        return []
    rows = []
    for line in p.read_text().splitlines()[1:]:
        f = line.split('\t')
        if len(f) != len(LIST_COLUMNS):
            continue
        r = dict(zip(LIST_COLUMNS, f))
        for k in ('w', 'h', 'mips', 'au', 'tile', 'selected'):
            r[k] = int(r[k])
        rows.append(r)
    return rows


def write_list(cache, rows):
    tmp = cache / '.list.tsv.part'
    tmp.write_text('\t'.join(LIST_COLUMNS) + '\n' +
                   ''.join('\t'.join(str(r[c]) for c in LIST_COLUMNS) + '\n' for r in rows))
    os.replace(tmp, cache / 'list.tsv')


def stage_list(cache, files):
    """list.tsv and png/<hash>.png from the game files (the first file naming a key wins). Skipped when the files
    are the ones listed last time and every selected PNG is there."""
    import texread
    import upscale
    sig = [[rel, p.stat().st_size, int(p.stat().st_mtime)] for p, rel in files]
    sigp = cache / 'list.json'
    png = cache / 'png'
    png.mkdir(parents=True, exist_ok=True)
    rows = read_list(cache)
    if rows and sigp.is_file() and json.loads(sigp.read_text()) == sig and \
            all((png / f"{r['key'][:8]}.png").is_file() for r in rows if r['selected']):
        return rows
    say(f"reading your game's textures ({len(files)} files)")
    rows, seen, errors = [], set(), 0
    for p, rel in files:
        try:
            texs = texread.textures(p)
        except Exception as e:   # a file neither reader accepts: its textures are left out
            errors += 1
            print(f'{rel}: not read ({str(e)[:100]})', flush=True)
            continue
        for t in texs:
            if t['key'] in seen:
                continue
            seen.add(t['key'])
            info = {'key': t['key'], 'name': t['name'], 'w': t['w'], 'h': t['h'], 'fmt': t['fmt'], 'mips': t['mips'],
                    'au': t['alpha_usage'], 'tile': t['tile'], 'cls': t['cls'], 'group': upscale.group_of(rel),
                    'file': rel}
            ok, why = upscale.selected(info)
            if ok and t['top'] is None:
                ok, why = False, 'no image data'
            out = png / f"{t['hash']}.png"
            if ok and not out.is_file():
                try:
                    tmp = png / f".{t['hash']}.png.part"
                    texread.to_rgba(t).save(tmp, format='PNG')
                    os.replace(tmp, out)
                except Exception as e:
                    ok, why = False, f'not decoded ({str(e)[:60]})'
            info.update(selected=int(ok), why=why.replace('\t', ' '))
            info['name'] = info['name'].replace('\t', ' ')
            rows.append(info)
    write_list(cache, rows)
    sigp.write_text(json.dumps(sig))
    say(f"{sum(r['selected'] for r in rows)} of {len(rows)} textures to upscale" +
        (f' ({errors} files not read)' if errors else ''))
    return rows


def read_manifest(path):
    """Records of a .jsonl manifest, last record per key wins (a torn last line from a stopped run is ignored)."""
    recs = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and 'key' in r:
                recs[r['key']] = r
    return recs


def append(path, rec):
    with open(path, 'a') as f:
        f.write(json.dumps(rec, sort_keys=True) + '\n')
        f.flush()


def stage_upscale(cache, rows, model_path=None, limit=0, model_fn=None):
    """Every selected texture not yet in pack/manifest.jsonl (errors are tried again), smallest first. Returns the
    number of textures written."""
    import upscale
    pack = cache / 'pack'
    pack.mkdir(parents=True, exist_ok=True)
    man = pack / 'manifest.jsonl'
    done = {k for k, r in read_manifest(man).items() if 'error' not in r}
    work = sorted((r for r in rows if r['selected'] and r['key'] not in done), key=lambda r: (r['w'] * r['h'], r['key']))
    if limit:
        work = work[:limit]
    if not work:
        return 0
    sel = {r['key'] for r in rows if r['selected']}
    total, n_done = len(sel), len(done & sel)
    if model_fn is None and model_path and any(upscale.method_for(r) == 'model' for r in work):
        say('starting the upscaler')
        model_fn = upscale.model_upscaler(model_path)
    times = {'model': [], 'lanczos': []}
    left = {'model': sum(upscale.method_for(r) == 'model' for r in work)}
    left['lanczos'] = len(work) - left['model']
    last, written = time.time(), 0
    for i, r in enumerate(work):
        key = r['key']
        info = {'name': r['name'], 'cls': r['cls'], 'tile': r['tile'], 'au': r['au'], 'group': r['group']}
        method = upscale.method_for(info)
        t0 = time.time()
        try:
            rec = upscale.process_one(key, info, cache / 'png' / f'{key[:8]}.png', pack, model_fn)
            rec.update(group=r['group'], file=r['file'])
            if 'format' in rec:
                written += 1
        except Exception as e:   # recorded; the batch goes on (tried again on the next run)
            rec = {'key': key, 'name': r['name'], 'error': repr(e)[:200]}
            print(f'{key} {r["name"]}: ERROR {repr(e)[:120]}', flush=True)
        append(man, rec)
        times[method].append(time.time() - t0)
        left[method] -= 1
        n = n_done + i + 1
        if (i + 1) % PROGRESS_EVERY == 0 or time.time() - last >= PROGRESS_SECONDS or i + 1 == len(work):
            last = time.time()
            rate = {m: (sum(t) / len(t) if t else 0.05) for m, t in times.items()}
            eta = sum(rate[m] * left[m] for m in left)
            say(f'{n} of {total} (model {rate["model"]:.1f} s/tex; about {max(int(eta // 60), 0)} min left)'
                if times['model'] else f'{n} of {total} (about {max(int(eta // 60), 0)} min left)')
    return written


def stage_hero(cache, photos_dir):
    """The photo grain on every matching pack texture not yet in pack/hero.jsonl."""
    import hero
    pack = cache / 'pack'
    man = read_manifest(pack / 'manifest.jsonl')
    hpath = pack / 'hero.jsonl'
    done = read_manifest(hpath)
    photos = hero.Photos(photos_dir)
    names = {r['key']: r for r in read_list(cache)}
    todo = [k for k, r in sorted(man.items()) if 'format' in r and k not in done
            and hero.rule_for(r.get('name', ''), names.get(k, {}).get('au')) is not None]
    if not todo:
        return 0
    say(f'grain overlays ({len(todo)} textures)')
    n = 0
    for key in todo:
        r = man[key]
        rule = hero.rule_for(r.get('name', ''), names.get(key, {}).get('au'))
        if not photos.has(rule[2]):
            continue   # tried again once the photo is there
        try:
            rec = hero.apply(pack / f'{key}.dds', r.get('name', ''), names.get(key, {}).get('au'), photos)
        except Exception as e:
            rec = {'hero_skip': f'error {repr(e)[:120]}'}
        rec['key'] = key
        if 'hero' in rec:
            rec['output_sha256'] = sha(pack / f'{key}.dds')
            n += 1
        append(hpath, rec)
    say(f'grain overlays: {n} textures')
    return n


def stage_pack(cache):
    """pack/index.tsv from the manifest (rows whose DDS is there and well formed), written only when it changes;
    pack/pack.json with the totals. Returns (rows, bytes)."""
    import dds
    pack = cache / 'pack'
    man = read_manifest(pack / 'manifest.jsonl')
    rows, size = [], 0
    for key, r in sorted(man.items()):
        f = pack / f'{key}.dds'
        if 'format' not in r or not f.is_file():
            continue
        try:
            dds.read_header(f)
        except ValueError:
            continue
        rows.append(f'{key}\talbedo\t{key}.dds')
        size += f.stat().st_size
    text = INDEX_HEADER + '\n' + ''.join(r + '\n' for r in rows)
    idx = pack / 'index.tsv'
    if not idx.is_file() or idx.read_text() != text:
        tmp = pack / '.index.tsv.part'
        tmp.write_text(text)
        os.replace(tmp, idx)
        (pack / 'pack.json').write_text(json.dumps({'textures': len(rows), 'bytes': size,
                                                    'model': MODEL['name'], 'model_sha256': MODEL['sha256'],
                                                    'made': time.strftime('%Y-%m-%d')}) + '\n')
    return len(rows), size


def worker(a):
    sys.path.insert(0, str(HERE))
    cache = cache_root(a.cache)
    rows = stage_list(cache, inputs(a.game, a.stuff))
    if not any(r['selected'] for r in rows):
        say('stopped: no textures found in the game folder')
        raise SystemExit(1)
    stage_upscale(cache, rows, a.model, a.limit)
    stage_hero(cache, cache / 'photos')
    n, size = stage_pack(cache)
    say(f'pack ready ({n} textures, {size / 2 ** 30:.1f} GB)')


# --- desk checks (design 3.1 items 1-4) ---

def _decode_level(path, fmt, w, h, level):
    """One mip level of a pack DDS as an RGBA numpy array (through Pillow's DDS reader)."""
    import io
    import numpy as np
    from PIL import Image
    import dds
    off = dds.HEADER_BYTES + sum(dds.level_size(fmt, max(w >> i, 1), max(h >> i, 1)) for i in range(level))
    lw, lh = max(w >> level, 1), max(h >> level, 1)
    with open(path, 'rb') as f:
        f.seek(off)
        data = f.read(dds.level_size(fmt, lw, lh))
    one = dds.encode_file(fmt, lw, lh, [data])
    return np.asarray(Image.open(io.BytesIO(one)).convert('RGBA'))


def check(cache, decode=True):
    """Prints one line per check and returns the list of failures (empty: the pack passes)."""
    sys.path.insert(0, str(HERE))
    import dds
    import upscale
    pack = cache / 'pack'
    fails, flags = [], []
    idx = pack / 'index.tsv'
    if not idx.is_file():
        say(f'check: no pack yet ({idx})')
        return ['no pack/index.tsv']
    rows = [l.split('\t') for l in idx.read_text().splitlines() if l and not l.startswith('#')]
    keys = [r[0] for r in rows]
    files = {p.name for p in pack.glob('*.dds')}
    man = read_manifest(pack / 'manifest.jsonl')
    hero_done = {k for k, r in read_manifest(pack / 'hero.jsonl').items() if 'hero' in r}
    lst = {r['key']: r for r in read_list(cache)}
    expected = sum(1 for r in lst.values() if r['selected'])
    # 1. counts
    if len(set(keys)) != len(keys):
        fails.append('a key is listed twice')
    missing = [r[2] for r in rows if len(r) != 3 or r[2] not in files]
    if missing:
        fails.append(f'{len(missing)} index rows without their file')
    if len(files) != len(rows):
        fails.append(f'{len(files)} DDS files for {len(rows)} index rows')
    if expected and len(rows) < EXPECTED_SHARE * expected:
        fails.append(f'{len(rows)} rows for {expected} selected textures (< {EXPECTED_SHARE:.0%})')
    say(f'check counts: {len(rows)} rows, {len(files)} files, {expected} selected')
    # 2. sizes, 3. formats
    for key, _slot, fname in (r for r in rows if len(r) == 3):
        f = pack / fname
        try:
            fmt, w, h, levels, _n = dds.read_header(f)
        except (OSError, ValueError) as e:
            fails.append(f'{fname}: {e}')
            continue
        m = man.get(key, {})
        src = lst.get(key)
        if max(w, h) > upscale.CAP:
            fails.append(f'{fname}: {w}x{h} over {upscale.CAP}')
        if src and (w, h) != (src['w'] * upscale.scale_for(src['w'], src['h']),
                              src['h'] * upscale.scale_for(src['w'], src['h'])):
            fails.append(f"{fname}: {w}x{h} from {src['w']}x{src['h']}")
        if levels != dds.full_chain(w, h):
            fails.append(f'{fname}: {levels} mip levels, a full chain is {dds.full_chain(w, h)}')
        if fmt == 'bc1' and (m.get('alpha') != 'none' or m.get('normal_map')):
            fails.append(f'{fname}: BC1 with alpha {m.get("alpha")} normal {m.get("normal_map")}')
        if decode and m.get('alpha') == 'cutout' and key not in hero_done:
            for lv in range(levels):
                a = _decode_level(f, fmt, w, h, lv)[..., 3]
                if ((a >= CUTOUT_SLACK) & (a <= 255 - CUTOUT_SLACK)).any():
                    fails.append(f'{fname}: cutout alpha not 0/255 at mip {lv}')
                    break
        if decode and m.get('normal_map'):
            import numpy as np
            n = _decode_level(f, fmt, w, h, 0)[..., :3].astype(np.float32) / 127.5 - 1.0
            err = float(np.abs(np.linalg.norm(n, axis=2) - 1.0).mean())
            if err > 0.02:
                fails.append(f'{fname}: normal map {err:.1%} off unit length')
        mo = m.get('mosaic')
        if mo and (mo.get('hf_ratio', 0) > upscale.MOSAIC_HF or mo.get('block', 0) > upscale.MOSAIC_BLOCK):
            flags.append(f"{key} {m.get('name', '')}: hf {mo.get('hf_ratio')} block {mo.get('block')}")
    say('check sizes and formats: ' + ('ok' if not fails else f'{len(fails)} problems'))
    # 4. mosaic tripwire (flags for an eye check, not failures)
    say(f'check mosaic tripwire: {len(flags)} model textures to look at')
    for f in fails[:40]:
        print('  FAIL ' + f, flush=True)
    for f in flags[:40]:
        print('  LOOK ' + f, flush=True)
    return fails


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--game', type=Path, help='your game folder (read only)')
    ap.add_argument('--stuff', type=Path, action='append', default=[],
                    help='a folder holding the Xbox 360 Stuff Pack download (optional; repeatable)')
    ap.add_argument('--cache', type=Path, help=f'the work folder (default ~/Library/NFSMW-Native-Setup/hd, or ${CACHE_ENV})')
    ap.add_argument('--limit', type=int, default=0, help='upscale at most N textures this run (tests)')
    ap.add_argument('--check', action='store_true', help='desk checks of the finished pack')
    ap.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    ap.add_argument('--model', type=Path, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    cache = cache_root(a.cache)
    if a.check:
        return 1 if check(cache) else 0
    if a.game is None:
        ap.error('--game is needed')
    if a.worker:
        worker(a)
        return 0
    cache.mkdir(parents=True, exist_ok=True)
    try:
        free = shutil.disk_usage(cache).free / 2 ** 30
        if free < FREE_GB:
            raise SystemExit(f'{free:.0f} GB free; the HD textures need about {FREE_GB} GB')
        py = tools(cache)
        model = fetch_model(cache)
        fetch_photos(cache)
        rc = run_worker(py, a, cache, model)
    except SystemExit as e:
        if e.code not in (None, 0):
            say(f'stopped: {e.code}')
        return 1
    except Exception as e:   # never a traceback in the player's setup window
        say(f'stopped: {str(e)[:160]}')
        return 1
    return rc


if __name__ == '__main__':
    sys.exit(main())
