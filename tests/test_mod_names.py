"""tools/setup_kit/mod_names.py + tpk.py (2.0.4): the Mods folder's game texture list, made by Setup from the player's
own game files: hashes and sizes only, truncated 23-character names mapped, linked folders followed, unreadable files
skipped. Needs no game files: small synthetic texture packs are used. Also the plumbing: the setup step never fails
the setup, package.py copies the list to Resources/ModNames/known.tsv, the core mod finds it there."""
import importlib.util, re, struct, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mn = load('mod_names', 'tools/setup_kit/mod_names.py')
setup = load('setup_kit_setup', 'tools/setup_kit/setup.py')


def chunk(cid, body):
    return struct.pack('<II', cid, len(body)) + body


def info_record(name, h, w, ht):
    r = bytearray(0x7C)
    r[0x0C:0x0C + len(name)] = name.encode('latin-1')[:24]
    struct.pack_into('<I', r, 0x24, h)
    struct.pack_into('<hh', r, 0x44, w, ht)
    return bytes(r)


def tpk_file(textures):
    """A chunk stream with one TPK: header, and one TextureInfo record per (stored name, hash, w, h)."""
    recs = b''.join(info_record(*t) for t in textures)
    info = chunk(0x33310001, bytes(0x7C)) + chunk(0x33310004, recs)
    return chunk(0xB3300000, chunk(0xB3310000, info) + chunk(0xB3320000, b''))


def jdlz_literal(data):
    """JDLZ with literals only (control bits 0), enough to exercise the reader's whole-file path."""
    body = bytearray([0, 0])
    for i, b in enumerate(data):
        body.append(b)
        if (i + 1) % 8 == 0 and i + 1 < len(data):
            body.append(0)
    total = 16 + len(body)
    return b'JDLZ\x02\x10\x00\x00' + struct.pack('<II', len(data), total) + bytes(body)


def test_hash_is_the_games_bstringhash():
    assert mn.bstringhash('') == 0xFFFFFFFF
    assert mn.bstringhash('A') == (0xFFFFFFFF * 33 + 65) & 0xFFFFFFFF


def test_list_holds_hashes_sizes_and_truncated_names(tmp_path):
    game = tmp_path / 'game'
    (game / 'GLOBAL').mkdir(parents=True)
    (game / 'SOUND').mkdir()
    long_name = 'HUD_A_VERY_LONG_TEXTURE_NAME_ICON'
    stem = long_name[:23]
    (game / 'GLOBAL/GLOBALB.BUN').write_bytes(tpk_file([
        ('RADAR_ICON', mn.bstringhash('RADAR_ICON'), 64, 32),
        (stem, mn.bstringhash(long_name), 128, 128),       # stored cut to 23 characters, hashed whole
    ]))
    (game / 'GLOBAL/GlobalB.lzc').write_bytes(jdlz_literal(tpk_file([('LOGO', mn.bstringhash('LOGO'), 256, 256)])))
    (game / 'SOUND/SKIPPED.BIN').write_bytes(tpk_file([('NOT_LISTED', 1, 8, 8)]))
    (game / 'GLOBAL/BROKEN.BIN').write_bytes(b'JDLZ\x02\x10' + bytes(4))   # unreadable: skipped, counted
    out = tmp_path / 'ModNames/known.tsv'
    assert mn.main([str(out), str(game)]) == 0
    text = out.read_text()
    assert text.startswith('# player-mods game texture list')
    rows = [line.split('\t') for line in text.splitlines() if not line.startswith('#')]
    k = {(r[1], int(r[2]), int(r[3])) for r in rows if r[0] == 'k'}
    t = {(r[1], r[2]) for r in rows if r[0] == 't'}
    assert k == {(f"{mn.bstringhash('RADAR_ICON'):08x}", 64, 32), (f"{mn.bstringhash(long_name):08x}", 128, 128),
                 (f"{mn.bstringhash('LOGO'):08x}", 256, 256)}
    assert t == {(f'{mn.bstringhash(stem):08x}', f'{mn.bstringhash(long_name):08x}')}
    assert 'RADAR_ICON' not in text and 'LOGO' not in text   # hashes and sizes only, no names


def test_follows_setups_linked_game_folders(tmp_path):
    real = tmp_path / 'real/GLOBAL'
    real.mkdir(parents=True)
    (real / 'A.BIN').write_bytes(tpk_file([('X', 0x1234, 4, 4)]))
    retail = tmp_path / 'original/retail'
    retail.mkdir(parents=True)
    (retail / 'GLOBAL').symlink_to(real, target_is_directory=True)
    assert [p.name for p in mn.pack_files(retail)] == ['A.BIN']


def test_no_textures_writes_nothing(tmp_path):
    out = tmp_path / 'known.tsv'
    assert mn.main([str(out), str(tmp_path / 'missing')]) == 1
    assert not out.exists()
    r = subprocess.run([sys.executable, ROOT / 'tools/setup_kit/mod_names.py'], capture_output=True, text=True)
    assert r.returncode == 2


def test_setup_step_never_fails_the_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, 'ROOT', tmp_path)
    monkeypatch.setattr(setup, 'LOG', tmp_path / 'setup.log')
    out = tmp_path / 'build/modnames/known.tsv'
    assert setup.mod_names_list(tmp_path / 'no-overlay', out=out) is None   # no game files: said, not fatal
    assert 'still load, unchecked' in (tmp_path / 'setup.log').read_text()
    (tmp_path / 'original/retail/GLOBAL').mkdir(parents=True)
    (tmp_path / 'original/retail/GLOBAL/A.BIN').write_bytes(tpk_file([('X', 0x1234, 4, 4)]))
    assert setup.mod_names_list(tmp_path / 'no-overlay', out=out) == out
    assert 'k\t00001234\t4\t4' in out.read_text()


def test_plumbing_setup_package_core_mod_kit():
    s = (ROOT / 'tools/setup_kit/setup.py').read_text()
    assert 'mod_names_list(gen / "x360fe")' in s and '"--mod-names", mod_names' in s
    p = (ROOT / 'tools/setup_kit/package.py').read_text()
    assert '--mod-names' in p and 'res / "ModNames/known.tsv"' in p
    core = (ROOT / 'mods/core/nfsmw/remaster_tex.h').read_text()
    assert '/../ModNames/known.tsv' in core and 'RECOMP_REMASTER_MODS_KNOWN' in core
    toml = (ROOT / 'mods/core/nfsmw/mod.toml').read_text()
    line = next(x for x in toml.splitlines() if x.startswith('player_mods ='))
    assert 'default = true' in line and '\\' not in line
    kit = (ROOT / 'kit/host/gpu/metal/remaster_tex.h').read_text()
    assert '#include "remaster_mods.h"' in kit and re.search(r'read_dds\(j\.file, keys_\.is_mod',
                                                            (ROOT / 'kit/host/gpu/metal/remaster_cache.h').read_text())
