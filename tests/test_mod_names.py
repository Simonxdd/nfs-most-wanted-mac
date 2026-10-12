"""tools/setup_kit/mod_names.py + tpk.py (2.0.4): the Mods folder's game texture list, made by Setup from the player's
own game files: hashes and sizes only, truncated 23-character names mapped, linked folders followed, unreadable files
skipped, the HUD styles' HUFF-packed compressed TPKs read. Needs no game files: small synthetic texture packs are used. Also the plumbing: the setup step never fails
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
tpk = load('tpk_reader', 'tools/setup_kit/tpk.py')
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


class BitWriter:
    def __init__(self):
        self.bits = []

    def put(self, value, width):
        self.bits += [(value >> (width - 1 - i)) & 1 for i in range(width)]

    def number(self, n):   # the reader's variable-length number: (w - 2) zeros, a one, then w bits
        w = 2
        while n >= (1 << (w + 1)) - 4:
            w += 1
        self.put(0, w - 2)
        self.put(1, 1)
        self.put(n - ((1 << w) - 4), w)

    def bytes(self):
        b = self.bits + [0] * (-len(self.bits) % 8 + 32)
        return bytes(int(''.join(map(str, b[i:i + 8])), 2) for i in range(0, len(b), 8))


def huff_pack(data, clue=0xFF):
    """HUFF with an 8-bit code for every byte (code = byte), the clue escaping literal clue bytes, one run, and the end
    marker: enough to exercise the reader's table, permutation, run and terminator paths."""
    w = BitWriter()
    w.put(0x30FB, 16)
    w.put(len(data), 24)
    w.put(clue, 8)
    for _ in range(7):
        w.number(0)        # widths 1..7: no codes
    w.number(256)          # width 8: all 256 symbols
    for _ in range(256):
        w.number(0)        # symbols in order 0..255
    i = 0
    while i < len(data):
        b = data[i]
        run = 0
        while i > 0 and i + run < len(data) and data[i + run] == data[i - 1] and run < 40:
            run += 1
        if run >= 4:
            w.put(clue, 8)
            w.number(run)
            i += run
            continue
        if b == clue:
            w.put(clue, 8)
            w.number(0)
            w.put(0, 1)
            w.put(b, 8)
        else:
            w.put(b, 8)
        i += 1
    w.put(clue, 8)
    w.number(0)
    w.put(1, 1)
    body = w.bytes()
    return b'HUFF\x01\x10\x00\x00' + struct.pack('<II', len(data), len(body)) + body


def compressed_tpk_file(name, h, w, ht):
    """A compressed TPK (as the HUD styles): one 33310003 row pointing at a HUFF block of [image | TextureInfo |
    format record]."""
    image = bytes([7] * 40) + bytes([0xFF, 1, 2, 3]) * 4
    blk = huff_pack(image + info_record(name, h, w, ht) + bytes(0x20))
    row_len = 8 + 24
    info = chunk(0x33310001, bytes(0x7C)) + bytes(row_len)
    off = 8 + (8 + len(info)) + 8 + 8   # from the TPK chunk header to the block
    row = chunk(0x33310003, struct.pack('<6I', h, off, len(blk) - 16, len(blk), 0, 0))
    info = chunk(0x33310001, bytes(0x7C)) + row
    return chunk(0xB3300000, chunk(0xB3310000, info) + chunk(0xB3320000, chunk(0x33320002, blk)))


def test_huff_round_trip():
    data = bytes(range(256)) + bytes([9] * 30) + b'\xff\xff abc'
    assert tpk.huff(huff_pack(data)) == data


def test_hud_style_compressed_tpk_is_listed(tmp_path):
    game = tmp_path / 'game/GLOBAL'
    game.mkdir(parents=True)
    (game / 'HUDS_Custom_00.bin').write_bytes(compressed_tpk_file('HUD_STYLE_ICON', mn.bstringhash('HUD_STYLE_ICON'),
                                                                  32, 16))
    assert [(t['hash'], t['w'], t['h']) for t in tpk.textures(game / 'HUDS_Custom_00.bin')] == \
        [(f"{mn.bstringhash('HUD_STYLE_ICON'):08x}", 32, 16)]
    out = tmp_path / 'known.tsv'
    assert mn.main([str(out), str(tmp_path / 'game')]) == 0
    assert f"k\t{mn.bstringhash('HUD_STYLE_ICON'):08x}\t32\t16" in out.read_text()


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
