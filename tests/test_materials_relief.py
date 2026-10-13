"""tools/hd_pack materials.py and relief.py (3.0): the Materials (ORM) and Wall relief (normal) packs Setup makes after
the HD textures. No model, no network, no game files: the shipped lists, the pinned model, the mip and cavity rules,
both steps end to end on a synthetic HD pack (a stand-in for the model and, without etcpak, for the BC7 encoder),
resume, index.tsv written last, the "skipped" line, NOTICE."""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HD = ROOT / 'tools/hd_pack'
sys.path.insert(0, str(HD))
np = pytest.importorskip('numpy')
Image = pytest.importorskip('PIL.Image')
import dds        # noqa: E402
import materials  # noqa: E402
import relief     # noqa: E402
import upscale    # noqa: E402

try:
    import etcpak   # noqa: F401
    HAVE_ETCPAK = True
except ImportError:
    HAVE_ETCPAK = False

# knowledge/INTAKE.md I3: the PBRify_Remix 1.7.2 zip, its RoughnessV2 .pth and the safetensors made from it (run 14)
INTAKE_ZIP = 'd787a88f644c542620f869e0c61e71f795488020fcece9a97e774ca2c2cecb0b'
INTAKE_PTH = '7003c39041af64cdb77d5120bd560a2878538fbb14a07657d8fd12aac5773679'
INTAKE_SAFE = 'ef8c91cd15724baf13cf95bd756649d4f4267b70899433de6ca76049da46ed9d'
PRIORS = {'road': 0.74, 'concrete': 0.70, 'brick': 0.85, 'cobble': 0.75, 'stone': 0.90, 'gravel': 0.90, 'dirt': 0.90,
          'sand': 0.90, 'metal': 0.45}


def mat_rows():
    return materials.read_keys(materials.KEYS_FILE, ('key', 'family', 'prior', 'metal', 'glint'))


def test_no_torch_at_import():
    assert 'torch' not in sys.modules and 'spandrel' not in sys.modules


def test_materials_list():
    lines = [l for l in materials.KEYS_FILE.read_text().splitlines() if l.strip() and not l.startswith('#')]
    assert all(len(l.split()) == 5 for l in lines)                 # hashes and numbers only: no names
    rows = mat_rows()
    keys = [r['key'] for r in rows]
    assert len(rows) == 249 and len(set(keys)) == 249
    assert all(re.fullmatch(r'[0-9a-f]{16}', k) for k in keys)
    for r in rows:
        assert r['family'] in materials.FAMILIES and r['metal'] in '01' and r['glint'] in '01', r
        allowed = {PRIORS[r['family']]} | ({0.60} if r['family'] in ('stone', 'cobble') else set())
        assert float(r['prior']) == (0.40 if r['metal'] == '1' else float(r['prior'])), r
        assert r['metal'] == '1' or float(r['prior']) in allowed, r
        assert (r['glint'] == '1') == (r['family'] in materials.GLINT_FAMILIES), r
    assert sum(r["metal"] == "1" for r in rows) == 11 and sum(r["glint"] == "1" for r in rows) == 186
    approved = set(upscale.approved_keys())
    assert set(keys) <= approved                                    # the HD pack only has approved keys


def test_relief_list():
    lines = [l for l in relief.KEYS_FILE.read_text().splitlines() if l.strip() and not l.startswith('#')]
    assert all(len(l.split()) == 3 for l in lines)
    rows = materials.read_keys(relief.KEYS_FILE, ('key', 'family', 'polarity'))
    keys = [r['key'] for r in rows]
    assert len(rows) == 5 and len(set(keys)) == 5 and set(keys) <= set(upscale.approved_keys())
    assert all(r['family'] in relief.GRAIN and r['polarity'] in ('dark_low', 'light_low') for r in rows)


def test_no_tracked_data_types():
    # the public repo's type scan: no .tsv/.dds/.pth/.safetensors files beside the tools
    bad = [p for p in HD.rglob('*') if p.suffix in ('.tsv', '.dds', '.pth', '.safetensors')]
    assert not bad, bad
    for p in (HD / 'materials.py', HD / 'relief.py', materials.KEYS_FILE, relief.KEYS_FILE):
        assert '/Users/' not in p.read_text()


def test_pinned_model():
    assert materials.ZIP['sha256'] == INTAKE_ZIP and materials.PTH['sha256'] == INTAKE_PTH
    assert materials.MODEL['sha256'] == INTAKE_SAFE and materials.MODEL['name'] == '1x-PBRify_RoughnessV2.safetensors'
    assert materials.ZIP['url'] == ('https://github.com/Kim2091/PBRify_Remix/releases/download/v1.7.2/'
                                    'PBRify_Remix-1.7.2.zip')
    assert materials.PTH['member'].endswith('Models/1x-PBRify_RoughnessV2.pth')
    src = (HD / 'materials.py').read_text()
    assert 'weights_only=True' in src
    assert 'CC0' in materials.MODEL['licence']


def test_bad_zip_refused(tmp_path):
    z = tmp_path / 'PBRify_Remix-1.7.2.zip'
    z.write_bytes(b'not the pinned zip')
    with pytest.raises(ValueError):
        materials.fetch_model(tmp_path / 'model', z)
    assert not (tmp_path / 'model' / materials.MODEL['name']).exists()


def test_mip_rule():
    rng = np.random.default_rng(1)
    r = rng.uniform(0.3, 0.9, (16, 64)).astype(np.float32)
    lv = materials.chain(r)
    assert len(lv) == dds.full_chain(64, 16) and lv[-1].shape == (1, 1)
    for a, b in zip(lv, lv[1:]):
        h, w = a.shape
        fy, fx = (2 if h > 1 else 1), (2 if w > 1 else 1)
        want = np.sqrt((a ** 2).reshape(h // fy, fy, w // fx, fx).mean((1, 3)))
        assert np.allclose(b, want, atol=1e-6)
    assert np.isclose((lv[-1] ** 2).mean(), (r ** 2).mean(), atol=1e-5)   # the mean GGX alpha is kept


def test_cavity_rule():
    rng = np.random.default_rng(2)
    rgb = rng.integers(0, 256, (64, 64, 3), dtype=np.uint8)
    cav, g = materials.cavity(rgb)
    assert abs(float((1 - cav).mean()) - materials.CAV_MEAN) < 1e-3 and g > 0
    assert cav.min() >= materials.CAV_FLOOR and cav.max() <= 1.0


def bricks(n):
    """A synthetic facade: light bricks, thin dark mortar lines, a little noise (RGBA, uint8)."""
    rng = np.random.default_rng(3)
    a = np.full((n, n, 4), 255, np.uint8)
    a[..., :3] = 170 + rng.integers(0, 30, (n, n, 1), dtype=np.uint8)
    a[::8, :, :3] = 60
    for row in range(0, n, 8):
        off = 0 if (row // 8) % 2 else 8
        a[row:row + 8, off::16, :3] = 60
    return a


def fake_hd(tmp_path, keys, side=64):
    """An HD cache: pack/index.tsv and one RGBA8 albedo DDS per key (4x stand-ins)."""
    pack = tmp_path / 'hd/pack'
    pack.mkdir(parents=True)
    rows = []
    for k in keys:
        a = bricks(side)
        levels, w = [], side
        while True:
            levels.append(np.ascontiguousarray(np.asarray(Image.fromarray(a).resize((w, w), Image.BOX))).tobytes())
            if w == 1:
                break
            w //= 2
        dds.write(pack / f'{k}.dds', 'rgba8', side, side, levels)
        rows.append(f'{k}\talbedo\t{k}.dds')
    (pack / 'index.tsv').write_text('# test\n' + '\n'.join(rows) + '\n')
    return tmp_path / 'hd'


def watch_index(monkeypatch, out, must_exist):
    """Wraps write_index: every other output file must already be there when index.tsv is written."""
    seen = []
    real = materials.write_index

    def wrapped(o, header, rows):
        assert all((out / f).is_file() for f in must_exist()), 'index.tsv written before the rest'
        assert not (out / 'index.tsv').exists() or seen
        seen.append(len(rows))
        return real(o, header, rows)
    monkeypatch.setattr(materials, 'write_index', wrapped)
    return seen


def test_materials_end_to_end_and_resume(tmp_path, monkeypatch, capsys):
    keys = ['00000000000000a1', '00000000000000b2', '00000000000000c3']
    hd = fake_hd(tmp_path, keys[:2])                                # c3 is not in the HD pack
    lst = tmp_path / 'keys.txt'
    lst.write_text('# key family prior metal glint\n00000000000000a1 brick 0.85 0 1\n'
                   '00000000000000b2 metal 0.40 1 1\n00000000000000c3 road 0.74 0 0\n')
    if not HAVE_ETCPAK:   # the BC7 encoder lives in the upscaler environment: a stand-in of the right size
        monkeypatch.setattr(upscale, 'encode', lambda levels, fmt: [
            bytes(dds.level_size(fmt, l[0].shape[1], l[0].shape[0])) for l in levels])
    out = tmp_path / 'materials'
    seen = watch_index(monkeypatch, out, lambda: ['manifest.json', 'glint.tsv'] + [f'{k}.dds' for k in keys[:2]])
    calls = []

    def fake_rough(rgb):            # stands in for RoughnessV2: a ramp around 0.5
        calls.append(rgb.shape)
        return np.linspace(0.3, 0.7, rgb.shape[0] * rgb.shape[1], dtype=np.float32).reshape(rgb.shape[:2])
    assert materials.run(hd, out, rough_fn=fake_rough, keys_file=lst) == 0
    assert calls == [(32, 32, 3), (32, 32, 3)]                       # half the albedo side
    assert seen == [2]
    idx = (out / 'index.tsv').read_text().splitlines()
    assert idx[0].startswith('#') and idx[1:] == [f'{k}\torm\t{k}.dds' for k in keys[:2]]
    assert (out / 'glint.tsv').read_text().splitlines()[1:] == ['00000000000000a1\tbrick\t0', '00000000000000b2\tmetal\t1']
    man = json.loads((out / 'manifest.json').read_text())
    assert man['rules'] == 'orm-rules-v3' and set(man['keys']) == set(keys[:2])
    for k, pr in (('00000000000000a1', 0.85), ('00000000000000b2', 0.40)):
        m = man['keys'][k]
        assert abs(m['mean'] - pr) < 0.01 and pr - 0.25 - 1e-6 <= m['p5'] <= m['p95'] <= pr + 0.25 + 1e-6
        assert dds.read_header(out / f'{k}.dds')[:4] == ('bc7', 32, 32, 6)
    if HAVE_ETCPAK:
        g = np.asarray(Image.open(out / '00000000000000b2.dds').convert('RGBA'))
        assert abs(g[..., 1].mean() / 255 - 0.40) < 0.02 and g[..., 2].min() >= 240
    assert 'not in the HD pack' in capsys.readouterr().out
    before = {p.name: p.stat().st_mtime_ns for p in out.glob('*.dds')}
    calls.clear()
    assert materials.run(hd, out, rough_fn=fake_rough, keys_file=lst) == 0   # resume: nothing made again
    assert calls == [] and {p.name: p.stat().st_mtime_ns for p in out.glob('*.dds')} == before
    assert not [p for p in out.iterdir() if p.name.startswith('.')]


def test_skipped_without_hd_pack(tmp_path, capsys):
    (tmp_path / 'hd').mkdir()
    assert materials.main(['--hd-cache', str(tmp_path / 'hd'), '--out', str(tmp_path / 'm')]) == 2
    assert relief.main(['--hd-cache', str(tmp_path / 'hd'), '--out', str(tmp_path / 'r')]) == 2
    assert capsys.readouterr().out.splitlines() == [
        'HD textures: materials skipped (the HD pack is not finished)',
        'HD textures: wall relief skipped (the HD pack is not finished)']
    assert not (tmp_path / 'm').exists() and not (tmp_path / 'r').exists()
    assert materials.hd_python(tmp_path / 'hd') is None


def test_relief_end_to_end(tmp_path, monkeypatch):
    keys = ['00000000000000d4', '00000000000000e5']
    hd = fake_hd(tmp_path, keys, side=128)
    lst = tmp_path / 'keys.txt'
    lst.write_text('# key family polarity\n00000000000000d4 brick dark_low\n00000000000000e5 concrete light_low\n')
    out = tmp_path / 'relief'
    seen = watch_index(monkeypatch, out, lambda: ['manifest.tsv'] + [f'{k}.dds' for k in keys])
    assert relief.run(hd, out, use_grain=False, keys_file=lst) == 0
    assert seen == [2]
    assert (out / 'index.tsv').read_text().splitlines()[1:] == [f'{k}\tnormal\t{k}.dds' for k in keys]
    man = (out / 'manifest.tsv').read_text().splitlines()
    assert man[0].split('\t') == list(relief.MANIFEST_COLUMNS)       # the kit reads ecos at columns 6-8, sig at 9-11
    for line in man[1:]:
        c = line.split('\t')
        f = out / f'{c[0]}.dds'
        assert dds.read_header(f)[:4] == ('rgba8', 64, 64, 7)
        ec = [float(v) for v in c[6:]]
        assert 0.5 < ec[2] < ec[1] < ec[0] < 1.0 and 0 < ec[3] < ec[4] < ec[5] < 1.0
        top = np.asarray(Image.open(f).convert('RGBA'))
        assert np.allclose(relief.terms(top.tobytes(), 64, 64), ec, atol=1e-5)   # computed from the map itself
        n = top[..., :3].astype(np.float32) / 127.5 - 1
        assert abs(float(np.linalg.norm(n, axis=2).mean()) - 1) < 0.02
    flat = np.zeros((4, 4, 4), np.uint8)
    flat[..., :3] = (128, 128, 255)
    t = relief.terms(flat.tobytes(), 4, 4)
    assert all(abs(v - 1) < 1e-3 for v in t[:3]) and all(v < 0.02 for v in t[3:])


def test_relief_matches_reference_recipe():
    """The polarity flips the relief; the grain pins match hdpack's photos."""
    y = bricks(64)[..., 0].astype(np.float32) / 255
    a, b = relief.structure(y, 1.0, 8.0), relief.structure(y, -1.0, 8.0)
    assert np.allclose(a[..., :2], -b[..., :2], atol=1e-5)
    assert abs(relief.rms_deg(a) - 8.0) < 1.0
    import hdpack
    assert set(relief.GRAIN.values()) <= set(hdpack.PHOTOS)


def test_progress_and_launch_lines():
    for src in ((HD / 'materials.py').read_text(), (HD / 'relief.py').read_text()):
        assert 'skipped (the HD pack is not finished)' in src and "write_index(" in src
    assert materials.PREFIX == 'HD textures: '
    assert "'--model-zip'" in (HD / 'materials.py').read_text()


def test_notice():
    notice = (ROOT / 'NOTICE').read_text()
    if 'RoughnessV2' not in notice:
        pytest.skip('NOTICE line for 1x-PBRify_RoughnessV2 not added yet')
    assert re.search(r'1x-PBRify_RoughnessV2[^.]*CC0 1\.0', notice)
