"""tools/hd_pack (2.1): the HD texture pack Setup makes on the player's Mac. No model, no network, no game files: the
recipe's rules (method, stage rules, alpha, padding, scale and cap), the DDS writer and reader, the TPK pixel path on
a synthetic file, one synthetic texture end to end through the Lanczos path with resume, and the plumbing (setup and
package flags, the Setup app's toggle and progress, NOTICE, pinned downloads)."""
import json
import re
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HD = ROOT / 'tools/hd_pack'
sys.path.insert(0, str(HD))
np = pytest.importorskip('numpy')
Image = pytest.importorskip('PIL.Image')
import dds       # noqa: E402
import hdpack    # noqa: E402
import hero      # noqa: E402
import texread   # noqa: E402
import upscale   # noqa: E402

try:
    import etcpak   # noqa: F401
    HAVE_ETCPAK = True
except ImportError:
    HAVE_ETCPAK = False

# knowledge/INTAKE.md I3: the adopted 4x-PBRify-UpscalerV4.safetensors
INTAKE_I3 = 'a00fa50116895a9b766aeb012edad0a14d66f3afda65539efc0f6fc28c991ba2'


def test_no_torch_at_import():
    assert 'torch' not in sys.modules and 'spandrel' not in sys.modules


def test_method_for():
    cases = [
        ({'name': 'SGN_HWY_EXIT', 'group': 'stream'}, 'lanczos'),
        ({'name': '_SGN_PARKING', 'group': 'stream'}, 'lanczos'),
        ({'name': 'MARKER_SPEEDTRAP', 'group': 'global'}, 'lanczos'),
        ({'name': 'QRACE_TRACK01', 'group': 'global'}, 'lanczos'),
        ({'name': 'MINIMAP_TILE', 'group': 'global'}, 'lanczos'),
        ({'name': 'FONT_MW_BODY', 'group': 'global'}, 'lanczos'),
        ({'name': 'OBJ_CAR_SEDAN_SKIN', 'group': 'stream'}, 'lanczos'),
        ({'name': 'TRAFPIZZA_SKIN', 'group': 'stream'}, 'lanczos'),
        ({'name': 'SOME_BODY', 'group': 'stream', 'cls': upscale.CAR_CLASS}, 'lanczos'),
        ({'name': 'ROAD_ASPHALT', 'group': 'cars'}, 'lanczos'),          # stage (c): every CARS texture
        ({'name': 'ARC_OLDBRICKB_WALLA', 'group': 'stream'}, 'model'),
        ({'name': 'TRN_GRASS', 'group': 'stuff'}, 'model'),
    ]
    for info, want in cases:
        assert upscale.method_for(info) == want, info
    assert upscale.KEEP_NOMOS2_SIGNS is False   # Q1: one model


def test_stage_rules_and_groups():
    g = upscale.group_of
    assert g('CARS/CARRERAGT/TEXTURES.BIN') == 'cars'
    assert g('TRACKS/STREAML2RA.BUN') == g('TRACKS/L2RA.BUN') == 'stream'
    assert g('TRACKS/L2RA/MINI_MAP_01.BIN') == g('GLOBAL/GLOBALB.BUN') == g('FRONTEND/FrontB.lzc') == 'global'
    assert g('TRACKS/TexWizardX360/textures.bin') == 'stuff'
    sel = lambda **k: upscale.selected(dict({'w': 64, 'h': 64, 'fmt': 'dxt1'}, **k), approved=None)[0]
    assert sel(name='SHD_TREE', group='stream') is False          # stage (b) leaves these to the game
    assert sel(name='FX_SMOKE', group='stream') is False
    assert sel(name='MINI_MAP', group='stream') is False
    assert sel(name='SHD_CAR', group='cars') is True               # stage (c) takes every car texture
    assert sel(name='FX_SMK06_BLEND', group='global') is True
    assert sel(name='DUMMY', group='global') is False
    assert sel(name='ROAD', group='stream', w=16) is False         # short side < 32
    assert sel(name='ROAD', group='stream', fmt='?') is False
    assert sel(name='ROAD', group='stream') is True


def test_alpha_class_and_override():
    opaque = np.full((8, 8), 255, np.uint8)
    cut = opaque.copy(); cut[0, 0] = 0
    mask = opaque.copy(); mask[0, 0] = 77
    assert [upscale.alpha_class(a) for a in (opaque, cut, mask)] == ['none', 'cutout', 'mask']
    assert upscale.alpha_class_for(mask, 1) == 'cutout'        # AlphaUsage wins
    assert upscale.alpha_class_for(cut, 2) == 'mask'
    assert upscale.alpha_class_for(mask, 0) == 'none'
    assert upscale.alpha_class_for(opaque, 1) == 'none'        # punch-through flagged, nothing transparent
    assert upscale.alpha_class_for(cut, None) == 'cutout'


def test_pad_modes():
    assert upscale.pad_modes({'tile': 3}) == ('wrap', 'wrap')
    assert upscale.pad_modes({'tile': 0}) == ('edge', 'edge')
    assert upscale.pad_modes({'tile': 1}) == ('edge', 'wrap')   # U wraps (axis 1)
    assert upscale.pad_modes({'tile': 2}) == ('wrap', 'edge')
    assert upscale.pad_modes({}) == ('wrap', 'wrap')
    img = np.arange(16, dtype=np.uint8).reshape(4, 4)
    p = upscale.pad2(img, 2, ('edge', 'wrap'))
    assert p.shape == (8, 8) and p[0, 2] == img[0, 0] and p[2, 0] == img[0, 2]


def test_scale_and_cap():
    s = upscale.scale_for
    assert s(256, 256) == 4 and 256 * 4 == 1024
    assert s(1024, 1024) == 2
    assert s(2048, 2048) == 2 and 2048 * 2 == upscale.CAP
    assert s(256, 2048) == 2                                     # 512x4096
    assert s(1023, 64) == 4 and s(4096, 64) == 1


@pytest.mark.parametrize('fmt', ['bc1', 'bc7', 'rgba8'])
@pytest.mark.parametrize('size', [(64, 32), (36, 20)])
@pytest.mark.parametrize('chain', [False, True])
def test_dds_round_trip(tmp_path, fmt, size, chain):
    w, h = size
    n = dds.full_chain(w, h) if chain else 1
    levels = [bytes([i]) * dds.level_size(fmt, max(w >> i, 1), max(h >> i, 1)) for i in range(n)]
    f = tmp_path / 't.dds'
    dds.write(f, fmt, w, h, levels)
    assert dds.read_header(f) == (fmt, w, h, n, sum(map(len, levels)))
    hdr = struct.unpack_from('<31I', f.read_bytes(), 4)
    assert hdr[2] == h and hdr[3] == w and hdr[6] == n
    assert hdr[4] == (dds.level_size(fmt, w, h) if fmt != 'rgba8' else w * 4)
    assert struct.unpack_from('<2I', f.read_bytes(), 128) == (dds.DXGI[fmt], 3)
    assert not list(tmp_path.glob('.*'))                         # no temporary left
    assert dds.full_chain(36, 20) == 6 and dds.full_chain(4096, 512) == 13
    with pytest.raises(ValueError):
        dds.write(tmp_path / 'bad.dds', fmt, w, h, [levels[0] + b'x'])
    f.write_bytes(f.read_bytes()[:-1])
    with pytest.raises(ValueError):
        dds.read_header(f)


def test_dds_rgba8_decodes(tmp_path):
    rgba = np.random.default_rng(1).integers(0, 256, (20, 36, 4), dtype=np.uint8)
    f = tmp_path / 'p.dds'
    dds.write(f, 'rgba8', 36, 20, [rgba.tobytes()])
    assert (np.asarray(Image.open(f).convert('RGBA')) == rgba).all()


def chunk(cid, payload):
    return struct.pack('<II', cid, len(payload)) + payload


def synthetic_tpk(path, name, rgba, au=1, tile=3):
    """One TPK with one a8r8g8b8 texture, laid out as the retail files are (texread's docstring)."""
    h, w = rgba.shape[:2]
    image = rgba[..., [2, 1, 0, 3]].tobytes()   # stored B, G, R, A
    rec = bytearray(0x7C)
    rec[0x0C:0x0C + len(name)] = name.encode()
    struct.pack_into('<7I', rec, 0x24, texread_hash(name), 0x1a93cf, 0, 0, 0, len(image), 0)
    struct.pack_into('<hh', rec, 0x44, w, h)
    rec[0x4E], rec[0x4F], rec[0x55] = 1, tile, au
    frec = bytearray(0x20)
    struct.pack_into('<I', frec, 0x14, 21)
    hdr = bytearray(0x7C)
    hdr[4:4 + 8] = b'TESTPACK'
    info = chunk(texread.INFO, chunk(0x33310001, bytes(hdr)) + chunk(0x33310004, bytes(rec)) + chunk(0x33310005, bytes(frec)))
    c1 = chunk(0x33320001, bytes(0x18))
    da = 8 + len(info) + 8 + len(c1) + 8
    data = chunk(texread.DATA, c1 + chunk(0x33320002, bytes((-da) % 0x80) + image))
    path.write_bytes(chunk(texread.TPK, info + data))


def texread_hash(name):
    v = 0xffffffff
    for b in name.encode():
        v = (v * 33 + b) & 0xffffffff
    return v


def cutout(size=64):
    rgba = np.zeros((size, size, 4), np.uint8)
    yy, xx = np.mgrid[:size, :size]
    rgba[..., 0] = (xx * 4) & 255
    rgba[..., 1] = (yy * 4) & 255
    rgba[..., 2] = 90
    rgba[..., 3] = np.where((xx - size / 2) ** 2 + (yy - size / 2) ** 2 < (size / 3) ** 2, 255, 0)
    return rgba


def test_texread_pixel_path(tmp_path):
    rgba = cutout(64)
    f = tmp_path / 'GLOBALB.BUN'
    synthetic_tpk(f, 'SGN_TEST', rgba)
    (t,) = texread.textures(f)
    assert (t['name'], t['w'], t['h'], t['fmt'], t['alpha_usage'], t['tile'], t['tpk']) == \
        ('SGN_TEST', 64, 64, 'a8r8g8b8', 1, 3, 'TESTPACK')
    assert t['key'] == f"{texread_hash('SGN_TEST'):08x}00000000"
    assert (np.asarray(texread.to_rgba(t)) == rgba).all()


def fake_game(root, rgba):
    (root / 'Global').mkdir(parents=True)
    synthetic_tpk(root / 'Global/GLOBALB.BUN', 'SGN_TEST', rgba)
    (root / 'TRACKS/TexWizardX360').mkdir(parents=True)
    (root / 'CARS/CARRERAGT').mkdir(parents=True)
    return root


def test_inputs_order_and_case(tmp_path):
    game = fake_game(tmp_path / 'game', cutout(64))
    (game / 'TRACKS/STREAML2RA.BUN').write_bytes(b'')
    (game / 'CARS/CARRERAGT/textures.bin').write_bytes(b'')
    (game / 'TRACKS/TexWizardX360/TEXTURES.BIN').write_bytes(b'')
    rels = [rel for _, rel in hdpack.inputs(game)]
    assert rels == ['GLOBAL/GLOBALB.BUN', 'TRACKS/STREAML2RA.BUN', 'CARS/CARRERAGT/textures.bin',
                    'TRACKS/TexWizardX360/textures.bin']
    assert upscale.group_of(rels[-1]) == 'stuff' and upscale.group_of(rels[2]) == 'cars'


@pytest.mark.skipif(not HAVE_ETCPAK, reason='etcpak (the BC1/BC7 encoder, installed only in the upscaler environment) '
                    'is not in this Python; no pure-Python encoder exists in the sources')
def test_lanczos_end_to_end_and_resume(tmp_path):
    game = fake_game(tmp_path / 'game', cutout(64))
    cache = tmp_path / 'cache'
    rows = hdpack.stage_list(cache, hdpack.inputs(game))
    assert [(r['name'], r['selected'], r['group']) for r in rows] == [('SGN_TEST', 1, 'global')]
    assert hdpack.stage_upscale(cache, rows) == 1                 # Lanczos path: no model needed
    n, size = hdpack.stage_pack(cache)
    key = rows[0]['key']
    pack = cache / 'pack'
    assert n == 1 and size > 0
    assert (pack / 'index.tsv').read_text().splitlines()[1:] == [f'{key}\talbedo\t{key}.dds']
    fmt, w, h, levels, _ = dds.read_header(pack / f'{key}.dds')
    assert (fmt, w, h, levels) == ('bc7', 256, 256, 9)
    for lv in range(levels):
        a = hdpack._decode_level(pack / f'{key}.dds', fmt, w, h, lv)[..., 3]
        assert not ((a >= hdpack.CUTOUT_SLACK) & (a <= 255 - hdpack.CUTOUT_SLACK)).any(), lv   # 0/255 up to BC7 rounding
    rec = hdpack.read_manifest(pack / 'manifest.jsonl')[key]
    assert (rec['method'], rec['alpha'], rec['scale'], rec['format']) == ('lanczos', 'cutout', 4, 'bc7')
    assert hdpack.check(cache) == []
    # resume: a second run writes nothing
    before = {p.name: p.stat().st_mtime_ns for p in pack.iterdir()}
    rows2 = hdpack.stage_list(cache, hdpack.inputs(game))
    assert hdpack.stage_upscale(cache, rows2) == 0
    assert hdpack.stage_hero(cache, cache / 'photos') == 0
    hdpack.stage_pack(cache)
    assert {p.name: p.stat().st_mtime_ns for p in pack.iterdir()} == before
    assert not [p for p in pack.iterdir() if p.name.startswith('.')]


def test_manifest_tolerates_a_torn_line(tmp_path):
    m = tmp_path / 'manifest.jsonl'
    hdpack.append(m, {'key': 'a', 'format': 'bc1'})
    hdpack.append(m, {'key': 'b', 'error': 'x'})
    with open(m, 'a') as f:
        f.write('{"key": "c", "form')                              # a setup stopped mid-write
    assert set(hdpack.read_manifest(m)) == {'a', 'b'}


def test_hero_rules():
    assert hero.rule_for('TRN_GRASS_A')[0] == 'grass'
    assert hero.rule_for('ARC_OLDBRICKB_WALLA')[0] == 'brick'
    assert hero.rule_for('WOOD_FENCE')[0] == 'wood' and hero.rule_for('TREE_TRUNK')[0] == 'bark'
    assert hero.rule_for('SGN_ROCK') is None and hero.rule_for('ROCK_NORMAL') is None
    assert hero.rule_for('TRN_GRASS_A', au=1) is None              # cutouts keep the base
    assert hero.rule_for('ARC_GLASS') is None


def test_progress_lines_and_cache(capsys, monkeypatch, tmp_path):
    hdpack.say('1 of 2 (about 0 min left)')
    assert capsys.readouterr().out == 'HD textures: 1 of 2 (about 0 min left)\n'
    monkeypatch.setenv(hdpack.CACHE_ENV, str(tmp_path))
    assert hdpack.cache_root() == tmp_path
    monkeypatch.delenv(hdpack.CACHE_ENV)
    assert hdpack.cache_root() == Path.home() / 'Library/NFSMW-Native-Setup/hd'
    src = HD.joinpath('hdpack.py').read_text()
    assert "say(f'pack ready (" in src and 'print(PREFIX + msg' in src


def test_pinned_downloads():
    hexes = re.compile(r'[0-9a-f]{64}')
    assert hexes.fullmatch(hdpack.MODEL['sha256']) and hdpack.MODEL['url'].startswith('https://')
    assert hdpack.MODEL['sha256'] == INTAKE_I3
    assert hdpack.PHOTO_URL.startswith('https://ambientcg.com/')
    assert set(hdpack.PHOTOS) == set(hero.MATERIALS)
    assert all(hexes.fullmatch(v) for v in hdpack.PHOTOS.values())
    req = [l for l in HD.joinpath('requirements.txt').read_text().splitlines() if l and not l.startswith('#')]
    assert all(re.fullmatch(r'[A-Za-z0-9_.-]+==[0-9][0-9A-Za-z.]*', l) for l in req), req
    assert 'spandrel==0.4.2' in req and 'torch==2.14.1' in req and 'etcpak==0.9.15' in req


def test_no_owner_paths():
    for p in HD.rglob('*'):
        if p.is_file() and p.suffix in ('.py', '.txt', '.md', '.sh'):
            assert '/Users/' not in p.read_text(), p


def test_notice():
    notice = (ROOT / 'NOTICE').read_text()
    assert '4x-PBRify-UpscalerV4' in notice and 'ambientCG' in notice
    assert re.search(r'PBRify, CC0 1\.0', notice) and re.search(r'ambientCG \(ambientcg\.com, CC0 1\.0\)', notice)
    assert ('Nomos2' in notice) == upscale.KEEP_NOMOS2_SIGNS


def test_plumbing():
    setup = (ROOT / 'tools/setup_kit/setup.py').read_text()
    assert '"--no-hd-textures"' in setup and '"--hd-textures"' in setup and 'HD_DEFAULT_ON = False' in setup   # 3.0, owner D-185 (was on, D-169)
    assert '"tools/hd_pack/hdpack.py"' in setup and '"--hd-pack", hd_pack' in setup
    assert 'stream(hd_cmd, "HD textures:")' in setup and 'line.startswith(prefix)' in setup   # 3.0: one helper for the steps
    package = (ROOT / 'tools/setup_kit/package.py').read_text()
    assert '"--hd-pack"' in package and 'res / "RemasterPack"' in package and '"hd_pack":' in package
    swift = (ROOT / 'tools/installer/SetupApp.swift').read_text()
    assert '"--no-hd-textures"' in swift and '"--hd-textures"' in swift and '"HD textures: "' in swift
    assert 'HD textures: pack ready' in swift and '@Published var hdTextures = false' in swift
    toml = (ROOT / 'mods/core/nfsmw/mod.toml').read_text()
    line = next(l for l in toml.splitlines() if l.startswith('remaster_textures'))
    assert 'no pack yet' not in line and 'Make HD textures in Setup' in line   # 3.0 wording
    # the mod looks for the pack where the packager puts it
    assert '%.*s/../RemasterPack' in (ROOT / 'mods/core/nfsmw/remaster_tex.h').read_text()


def test_setup_flags_parse():
    """--hd-textures / --no-hd-textures with HD_DEFAULT_ON as the default (Q2), without running anything."""
    import argparse
    src = (ROOT / 'tools/setup_kit/setup.py').read_text()
    block = src[src.index('    hd = ap.add_mutually_exclusive_group()'):src.index('    ap.add_argument("--out"')]
    for default in (False, True):
        ap = argparse.ArgumentParser()
        exec(re.sub(r'^    ', '', block, flags=re.M), {'ap': ap, 'HD_DEFAULT_ON': default})
        assert ap.parse_args([]).hd_textures is default
        assert ap.parse_args(['--hd-textures']).hd_textures is True
        assert ap.parse_args(['--no-hd-textures']).hd_textures is False
        with pytest.raises(SystemExit):
            ap.parse_args(['--hd-textures', '--no-hd-textures'])


def test_package_manifest_line(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location('package', ROOT / 'tools/setup_kit/package.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.hd_pack_line(tmp_path / 'index.tsv') is None
    (tmp_path / 'index.tsv').write_text('# header\na\talbedo\ta.dds\nb\talbedo\tb.dds\n')
    assert m.hd_pack_line(tmp_path / 'index.tsv') == ['# header', 2]
    json.dumps(m.hd_pack_line(tmp_path / 'index.tsv'))


needs_etcpak = pytest.mark.skipif(not HAVE_ETCPAK, reason='etcpak is not in this Python (upscaler environment only)')


@needs_etcpak
def test_model_path_with_a_stand_in_model(tmp_path):
    """The model path's pad, crop, scale and mosaic fields, with a nearest-neighbour 4x standing in for the model."""
    rng = np.random.default_rng(3)
    rgb = rng.integers(0, 256, (48, 1024, 3), dtype=np.uint8)
    src = tmp_path / 'src.png'
    Image.fromarray(rgb).save(src)
    seen = []

    def fake(padded):
        seen.append(padded.shape)
        return np.kron(padded, np.ones((4, 4, 1), np.uint8))
    rec = upscale.process_one('abcdef0100000000', {'name': 'ARC_WALL', 'tile': 1, 'au': 0, 'group': 'stream'},
                              src, tmp_path, fake)
    assert seen == [(48 + 2 * upscale.PAD, 1024 + 2 * upscale.PAD, 3)]
    assert (rec['method'], rec['scale'], rec['size'], rec['format'], rec['pad']) == \
        ('model', 2, [2048, 96], 'bc1', ['edge', 'wrap'])
    assert set(rec['mosaic']) == {'hf_ratio', 'block'}
    assert dds.read_header(tmp_path / 'abcdef0100000000.dds')[:4] == ('bc1', 2048, 96, 12)


@needs_etcpak
def test_hero_grain_on_a_pack_texture(tmp_path):
    rng = np.random.default_rng(5)
    base = np.clip(rng.normal(110, 18, (256, 256, 3)), 0, 255).astype(np.uint8)
    f = tmp_path / 'k.dds'
    dds.write(f, 'bc1', 256, 256, upscale.encode(upscale.mips(base, np.full((256, 256), 255, np.uint8), 'none'), 'bc1'))
    photos = tmp_path / 'photos'
    photos.mkdir()
    photo = np.clip(rng.normal(120, 40, (512, 512, 3)), 0, 255).astype(np.uint8)
    Image.fromarray(photo).save(photos / hero.photo_name('Grass004'), quality=90)
    ph = hero.Photos(photos)
    assert hero.apply(f, 'TRN_GRASS_A', 0, hero.Photos(tmp_path / 'none')) == {'hero_skip': 'photo Grass004 not downloaded'}
    rec = hero.apply(f, 'TRN_GRASS_A', 0, ph)
    assert rec['hero'] == 'grass' and rec['shift'] <= 3 and 0.9 <= rec['detail_ratio'] <= 2.5, rec
    assert dds.read_header(f)[:4] == ('bc7', 256, 256, 9)
    assert hero.apply(f, 'WOOD_PLANKS', 0, ph)['hero_skip'] == 'photo Wood051 not downloaded'


def test_approved_set():
    """Owner D-169 (12): only R1-dev's v6b keys (a hashes-only list, 16 hex digits each, no names)."""
    keys = upscale.approved_keys()
    assert keys and len(keys) == 3782
    assert all(len(k) == 16 and all(c in '0123456789abcdef' for c in k) for k in keys)
    text = upscale.APPROVED_FILE.read_text()
    assert not any(c.isalpha() and c not in 'abcdef' for c in text.replace('\n', ''))   # hashes only
    k = sorted(keys)[0]
    info = {'key': k, 'name': 'ARC_WALL', 'w': 64, 'h': 64, 'fmt': 'dxt1', 'group': 'global'}
    assert upscale.selected(info)[0] is True
    assert upscale.selected(dict(info, key='ffffffff00000000'))[0] is False
