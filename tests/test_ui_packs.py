"""tools/hd_pack HD HUD (hud_pack.py = ui_pack.py picture layer + ui2_pack.py drawing layer). No model, no network, no
game files: the steep-edge redraw on a synthetic alpha shape (and a soft glow it must leave alone), the opaque path,
a synthetic game end to end (index rows, written last, resume, the Stuff Pack's FrontB, tiles off by default), the
40 SVGs against their manifest, the drawing layer's desk check, and the one-step driver."""
import hashlib
import json
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HD = ROOT / 'tools/hd_pack'
sys.path.insert(0, str(HD))
np = pytest.importorskip('numpy')
Image = pytest.importorskip('PIL.Image')
import dds        # noqa: E402
import hud_pack   # noqa: E402
import texread    # noqa: E402
import ui2_pack   # noqa: E402
import ui_pack    # noqa: E402

try:
    import etcpak   # noqa: F401
    HAVE_ETCPAK = True
except ImportError:
    HAVE_ETCPAK = False
ROW = re.compile(r'^[0-9a-f]{16}\talbedo\t[0-9a-f]{16}\.dds$')


# --- synthetic inputs ---

def name_hash(name):
    v = 0xffffffff
    for b in name.encode():
        v = (v * 33 + b) & 0xffffffff
    return v


def chunk(cid, body):
    return struct.pack('<II', cid, len(body)) + body


def synthetic_tpk(path, texs):
    """One TPK holding [(name, rgba, alpha usage)] as a8r8g8b8 textures, laid out as texread reads the retail files."""
    recs, frecs, images = b'', b'', b''
    for name, rgba, au in texs:
        h, w = rgba.shape[:2]
        image = rgba[..., [2, 1, 0, 3]].tobytes()   # stored B, G, R, A
        rec = bytearray(0x7C)
        rec[0x0C:0x0C + len(name)] = name.encode()
        struct.pack_into('<7I', rec, 0x24, name_hash(name), 0x1a93cf, 0, len(images), 0, len(image), 0)
        struct.pack_into('<hh', rec, 0x44, w, h)
        rec[0x4E], rec[0x55] = 1, au
        frec = bytearray(0x20)
        struct.pack_into('<I', frec, 0x14, 21)
        recs, frecs, images = recs + bytes(rec), frecs + bytes(frec), images + image
    hdr = bytearray(0x7C)
    hdr[4:12] = b'TESTPACK'
    info = chunk(texread.INFO, chunk(0x33310001, bytes(hdr)) + chunk(0x33310004, recs) + chunk(0x33310005, frecs))
    c1 = chunk(0x33320001, bytes(0x18))
    da = 8 + len(info) + 8 + len(c1) + 8
    data = chunk(texread.DATA, c1 + chunk(0x33320002, bytes((-da) % 0x80) + images))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(chunk(texread.TPK, info + data))


def disc(size=32, soft=False):
    """A hard shape with a one-texel anti-aliased edge, or (soft) a glow fading over the whole texture."""
    yy, xx = np.mgrid[:size, :size] + 0.5
    r = np.hypot(xx - size / 2, yy - size / 2)
    a = np.clip(1 - r / (size / 2), 0, 1) if soft else np.clip(size / 3 - r + 0.5, 0, 1)
    rgba = np.zeros((size, size, 4), np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = 200, 120, 40
    rgba[..., 3] = np.round(a * 255)
    return rgba


def opaque(size=32):
    yy, xx = np.mgrid[:size, :size]
    rgba = np.full((size, size, 4), 255, np.uint8)
    rgba[..., 0], rgba[..., 1] = (xx * 8) & 255, (yy * 8) & 255
    return rgba


# Every name takes the RGBA8 path, so the end-to-end test needs no BC7 encoder.
GAME = {
    'GLOBAL/HUDTEXRACE.BIN': [('TACH_NEEDLE_00', disc(), 1), ('RADAR_LEDS_COLOR', opaque(), 0),
                              ('RADAR_LEDS_TINY', disc(8), 1)],
    'GLOBAL/HUDS_Custom_00.bin': [('REDLINE_MASK', disc(), 1)],
    'GLOBAL/InGameA.bun': [('MINIMAP_MASK', disc(), 1), ('NOT_A_HUD_TEXTURE', disc(), 1)],
    'GLOBAL/GlobalB.lzc': [('LED_MAIN', disc(), 1)],
    'GLOBAL/GLOBALA.BUN': [('FONT_MW_BODY', disc(), 1)],
    'FRONTEND/FrontB.lzc': [('FONT_MW_TITLE', disc(), 1)],
}


def fake_game(root):
    for rel, texs in GAME.items():
        synthetic_tpk(root / rel, texs)
    return root


def edge_width(a):
    """Texels strictly between 10 % and 90 % alpha."""
    f = a.astype(np.float32) / 255
    return int(((f > 0.1) & (f < 0.9)).sum())


# --- the recipe ---

def test_steep_edge_is_redrawn():
    a = disc(32)[..., 3]
    size = (128, 128)
    out, changed = ui_pack.redraw_alpha(a, size)
    smooth = np.asarray(Image.fromarray(a.astype(np.float32) / 255).resize(size, Image.BICUBIC))
    smooth8 = np.clip(smooth * 255 + 0.5, 0, 255).astype(np.uint8)
    assert out.shape == (128, 128) and changed > 0.02
    assert edge_width(out) < 0.6 * edge_width(smooth8)          # the outline is re-anti-aliased, thinner
    assert out[64, 64] == 255 and out[0, 0] == 0                # inside and outside keep their level
    assert abs(float(out.mean()) - float(smooth8.mean())) < 4   # same shape, not grown or shrunk


def test_soft_gradient_is_left_alone():
    a = disc(32, soft=True)[..., 3]
    out, changed = ui_pack.redraw_alpha(a, (128, 128))
    assert changed < 0.01


def test_classify_and_opaque_path():
    assert ui_pack.classify(opaque()[..., 3], 0) == 'opaque'
    assert ui_pack.classify(disc()[..., 3], 1) == 'shape'
    assert ui_pack.classify(disc(soft=True)[..., 3], 1) == 'soft'
    rgb, a, changed = ui_pack.upscale(opaque(), 'opaque')
    assert rgb.shape == (128, 128, 3) and (a == 255).all() and changed == 0


def test_bleed_fills_transparent_colour():
    rgba = disc()
    rgba[rgba[..., 3] <= 8, :3] = 0
    out = ui_pack.bleed(rgba[..., :3], rgba[..., 3])
    assert tuple(out[2, 16]) == (200, 120, 40)   # no black dragged in at the edge


def test_formats():
    for n in ('FONT_MW_BODY', 'MINIMAP_MASK', 'RADAR_LEDS_COLOR', 'LED_MAIN', 'TACH_NEEDLE_00', 'TURBO_NEEDLE_00',
              'REDLINE_MASK', 'HEATMETERMASKTOP'):
        assert ui_pack.fmt_for(n) == 'rgba8', n
    for n in ('ARROW', 'MINIMAP_ICON_CAR', 'TIME_BACKING', 'MINIMAP_MASK2'):
        assert ui_pack.fmt_for(n) == 'bc7', n


# --- the picture layer end to end ---

def index_rows(p):
    lines = Path(p).read_text().splitlines()
    assert lines[0].startswith('# ')
    return lines[1:]


def test_picture_layer_end_to_end_and_resume(tmp_path, capsys):
    game, out = fake_game(tmp_path / 'game'), tmp_path / 'hud/ui'
    assert ui_pack.main(['--game', str(game), '--out', str(out)]) == 0
    rows = index_rows(out / 'index.tsv')
    names = ['TACH_NEEDLE_00', 'RADAR_LEDS_COLOR', 'REDLINE_MASK', 'MINIMAP_MASK', 'LED_MAIN', 'FONT_MW_BODY',
             'FONT_MW_TITLE']
    want = [f'{name_hash(n):08x}00000000' for n in names]
    assert [r.split('\t')[0] for r in rows] == want              # filtered, under-16-px skipped, file order
    assert all(ROW.match(r) for r in rows)
    for k in want:
        assert dds.read_header(out / f'{k}.dds')[:4] == ('rgba8', 128, 128, 8)
    m = json.loads((out / 'manifest.json').read_text())
    assert m['skipped'][0]['name'] == 'RADAR_LEDS_TINY' and m['tiles'] is False
    assert {t['name']: t['cls'] for t in m['textures']}['RADAR_LEDS_COLOR'] == 'opaque'
    assert not (out / 'tiles').exists() and 'albedo_ondemand' not in (out / 'index.tsv').read_text()
    assert all(line.startswith('HUD textures: ') for line in capsys.readouterr().out.splitlines())
    # resume: nothing is made again
    before = {p.name: p.stat().st_mtime_ns for p in out.glob('*.dds')}
    assert ui_pack.main(['--game', str(game), '--out', str(out)]) == 0
    assert {p.name: p.stat().st_mtime_ns for p in out.glob('*.dds')} == before
    assert '(0 made, 7 kept from before)' in capsys.readouterr().out
    assert not list(out.glob('.*.part'))


def test_stuff_pack_frontb_is_used(tmp_path):
    game = fake_game(tmp_path / 'game')
    synthetic_tpk(tmp_path / 'stuff/FRONTEND/FrontB.lzc', [('FONT_MW_CUSTOM_NUMBERS', disc(), 1)])
    out = tmp_path / 'ui'
    assert ui_pack.main(['--game', str(game), '--stuff', str(tmp_path / 'stuff'), '--out', str(out)]) == 0
    m = json.loads((out / 'manifest.json').read_text())
    assert [t['name'] for t in m['textures'] if t['file'] == 'FRONTEND/FrontB.lzc'] == ['FONT_MW_CUSTOM_NUMBERS']


def test_missing_input_or_failure_leaves_no_index(tmp_path, monkeypatch):
    game, out = fake_game(tmp_path / 'game'), tmp_path / 'ui'
    assert ui_pack.main(['--game', str(game), '--out', str(out)]) == 0
    (game / 'GLOBAL/GLOBALA.BUN').unlink()
    assert ui_pack.main(['--game', str(game), '--out', str(out)]) == 1
    assert not (out / 'index.tsv').exists()                     # the old index goes until the layer is whole again
    fake_game(game)

    def boom(*a):
        raise RuntimeError('stopped')
    monkeypatch.setattr(ui_pack, 'write', boom)
    shutil.rmtree(out)
    assert ui_pack.main(['--game', str(game), '--out', str(out)]) == 1
    assert not (out / 'index.tsv').exists()


def test_tiles_flag_off_by_default(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(ui_pack, 'build', lambda game, out, stuff, tiles: seen.append(tiles) or 0)
    ui_pack.main(['--game', 'g', '--out', str(tmp_path)])
    ui_pack.main(['--game', 'g', '--out', str(tmp_path), '--tiles'])
    assert seen == [False, True]


@pytest.mark.skipif(not HAVE_ETCPAK, reason='etcpak (the BC7 encoder) is not in this Python')
def test_tiles_when_asked(tmp_path):
    game = fake_game(tmp_path / 'game')
    tiles = [(f'MINI_MAP_T_CHOP{n}', opaque(128), 0) for n in range(64)]
    synthetic_tpk(game / 'TRACKS/L2RA/MINI_MAP_T.BIN', tiles)
    out = tmp_path / 'ui'
    assert ui_pack.main(['--game', str(game), '--out', str(out), '--tiles']) == 0
    rows = [r.split('\t') for r in index_rows(out / 'index.tsv') if '\talbedo_ondemand\t' in r]
    assert len(rows) == 64 and all(f.startswith('tiles/') for _k, _s, f in rows)
    assert len({f for _k, _s, f in rows}) == 1                  # equal tiles share one file
    assert dds.read_header(out / rows[0][2])[:3] == ('bc7', 512, 512)
    assert ui_pack.main(['--game', str(game), '--out', str(out)]) == 0
    assert not (out / 'tiles').exists()                         # turned off again: the tiles go


# --- the drawing layer ---

def manifest():
    return json.loads(ui2_pack.MANIFEST.read_text())


def test_svg_manifest_matches_the_files():
    m = manifest()
    t = m['textures']
    assert len(t) == 40 and len({x['key'] for x in t}) == 40 and len({x['name'] for x in t}) == 40
    assert ui2_pack.check_svgs(m) == []
    for x in t:
        assert hashlib.sha256((ui2_pack.SVG_DIR / x['svg']).read_bytes()).hexdigest() == x['svg_sha256']
        assert re.fullmatch(r'[0-9a-f]{8}00000000', x['key'])
        assert x['size'] == [4 * x['source_size'][0], 4 * x['source_size'][1]]
        assert x['format'] == ui_pack.fmt_for(x['name'])
        assert 0 < x['alpha_coverage'] <= 1
    assert sorted(p.name for p in ui2_pack.SVG_DIR.glob('*.svg')) == sorted(x['svg'] for x in t)
    assert (ui2_pack.RENDERER).is_file()


def test_svgs_are_plain_vector_art():
    for p in ui2_pack.SVG_DIR.iterdir():
        assert p.suffix in ('.svg', '.json', '.swift'), p      # no .tsv/.dds/.png in the repo
        s = p.read_text()
        assert '/Users/' not in s
        if p.suffix == '.svg':
            assert not re.search(r'<image|<text|base64|href=|<script', s), p


def fake_svg_set(tmp_path, monkeypatch, n=2):
    """A manifest of n RGBA8 discs with a stand-in renderer (no swiftc)."""
    d = tmp_path / 'svg'
    d.mkdir()
    tex = []
    for i in range(n):
        name = f'TACH_NEEDLE_{i:02d}'
        (d / f'{name}.svg').write_text(f'<svg>{i}</svg>')
        a = disc(64)[..., 3]
        tex.append(dict(name=name, key=f'{name_hash(name):08x}00000000', file='x', source_size=[16, 16],
                        size=[64, 64], format='rgba8', svg=f'{name}.svg',
                        svg_sha256=hashlib.sha256((d / f'{name}.svg').read_bytes()).hexdigest(),
                        alpha_coverage=round(float(a.mean()) / 255, 5)))
    (d / 'manifest.json').write_text(json.dumps(dict(textures=tex)))
    monkeypatch.setattr(ui2_pack, 'SVG_DIR', d)
    monkeypatch.setattr(ui2_pack, 'MANIFEST', d / 'manifest.json')
    monkeypatch.setattr(ui2_pack, 'build_renderer', lambda cache: 'stand-in')
    monkeypatch.setattr(ui2_pack, 'render', lambda exe, svg, w, h: disc(w))
    return d


def test_drawing_layer_desk_check_and_index(tmp_path, monkeypatch, capsys):
    fake_svg_set(tmp_path, monkeypatch)
    ui, out = tmp_path / 'hud/ui', tmp_path / 'hud/ui2'
    assert ui2_pack.build(ui, out) == 1                         # needs the picture layer first
    ui.mkdir(parents=True)
    (ui / 'index.tsv').write_text('# x\n')
    monkeypatch.setattr(ui2_pack, 'desk_check', lambda o, m, c: [] if len(m['textures']) == 2 else ['count'])
    assert ui2_pack.build(ui, out) == 0
    rows = index_rows(out / 'index.tsv')
    assert len(rows) == 2 and all(ROW.match(r) for r in rows)
    assert all(line.startswith('HUD textures: ') for line in capsys.readouterr().out.splitlines())


def test_desk_check_rules(tmp_path, monkeypatch):
    fake_svg_set(tmp_path, monkeypatch)
    m = ui2_pack.load_manifest()
    out = tmp_path / 'ui2'
    out.mkdir()
    probs = ui2_pack.desk_check(out, m, {})
    assert any('expected 40' in p for p in probs) and any('not a DDS' in p or 'No such file' in p for p in probs)
    assert ui2_pack.coverage_ok(0.5, 0.52) and not ui2_pack.coverage_ok(0.4, 0.5)
    assert ui2_pack.coverage_ok(0.011, 0.01)                    # the floor for sparse textures
    # a changed SVG is refused before anything is made
    (ui2_pack.SVG_DIR / 'TACH_NEEDLE_00.svg').write_text('<svg>changed</svg>')
    (tmp_path / 'ui').mkdir()
    (tmp_path / 'ui/index.tsv').write_text('# x\n')
    assert ui2_pack.build(tmp_path / 'ui', out) == 1 and not (out / 'index.tsv').exists()


@pytest.mark.skipif(sys.platform != 'darwin' or not shutil.which('xcrun'), reason='CoreSVG and swiftc are macOS only')
def test_real_renderer_one_svg(tmp_path):
    exe = ui2_pack.build_renderer(tmp_path)
    t = next(x for x in manifest()['textures'] if x['name'] == 'MINIMAP_ICON_CAR')
    rgba = ui2_pack.render(exe, ui2_pack.SVG_DIR / t['svg'], *t['size'])
    assert rgba.shape == (t['size'][1], t['size'][0], 4)
    assert ui2_pack.coverage_ok(ui2_pack.coverage(rgba[..., 3]), t['alpha_coverage'])
    assert ui2_pack.build_renderer(tmp_path) == exe             # built once
    assert subprocess.run([str(exe)], capture_output=True).returncode == 2


# --- the one-step driver ---

def test_driver_pins_and_layers(tmp_path, monkeypatch, capsys):
    p = hud_pack.pins()
    assert [x.split('==')[0].lower() for x in p] == ['etcpak', 'numpy', 'pillow'] and all('==' in x for x in p)

    class A:
        game, out, stuff, tiles = tmp_path / 'g', tmp_path / 'hud', None, False
    calls = []
    monkeypatch.setattr(ui_pack, 'main', lambda argv: calls.append(argv) or 0)
    monkeypatch.setattr(ui2_pack, 'main', lambda argv: calls.append(argv) or 1)
    assert hud_pack.run_layers(A, tmp_path) == 0                # drawing layer failed: the picture layer stays
    assert calls[0][calls[0].index('--out') + 1] == str(tmp_path / 'hud/ui') and '--tiles' not in calls[0]
    assert calls[1][calls[1].index('--out') + 1] == str(tmp_path / 'hud/ui2')
    assert 'picture layer is used on its own' in capsys.readouterr().out
    monkeypatch.setattr(ui_pack, 'main', lambda argv: 1)
    assert hud_pack.run_layers(A, tmp_path) == 1
    out = capsys.readouterr().out
    assert out and all(line.startswith('HUD textures: ') for line in out.splitlines())
    assert 'HD HUD' in hud_pack.__doc__ and 'sharp HUD' not in hud_pack.__doc__


def test_no_owner_paths_in_new_files():
    for p in [HD / 'ui_pack.py', HD / 'ui2_pack.py', HD / 'hud_pack.py']:
        s = p.read_text()
        assert '/Users/' not in s and 'harness/' not in s and 'derived/' not in s, p
