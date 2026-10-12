"""tools/setup_kit/x360_stuff.py (2.0.4): the Xbox 360 Stuff Pack's front end is used only as the pack's exact files
(path, size, SHA-256), whole or not at all, found in an X360-Stuff / Easy Installation folder before a game folder's own
FRONTEND; everything else in the download is named as not used. Needs no pack files: small stand-ins are used. Also
the game side: the setting, the overlay push and Look = PC."""
import hashlib, importlib.util, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('x360_stuff', ROOT / 'tools/setup_kit/x360_stuff.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def stand_in(root, want, wrong=None):
    for rel in want:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'not the pack' if rel == wrong else rel.encode())


def want_of(rels):
    return {rel: (len(rel.encode()), hashlib.sha256(rel.encode()).hexdigest()) for rel in rels}


def test_the_set_is_the_packs_front_end():
    assert len(m.FRONT_END) == 8
    assert 'FRONTEND/FrontB.lzc' in m.FRONT_END and 'GLOBAL/GlobalB.lzc' in m.FRONT_END
    assert 'GLOBAL/GlobalMemoryFile.bin' in m.FRONT_END   # GlobalB only works with its memory-file stub
    assert sum(r.startswith('FRONTEND/PLATFORMS/') for r in m.FRONT_END) == 5
    assert all(len(d) == 64 and size > 0 for size, d in m.FRONT_END.values())
    assert not any(r.lower().endswith('attributes.bin') or r.lower().startswith('sound') for r in m.FRONT_END)


def test_exact_files_whole_or_nothing(tmp_path):
    rels = ['FRONTEND/FrontB.lzc', 'GLOBAL/GlobalB.lzc']
    want = want_of(rels)
    good = tmp_path / 'good'
    stand_in(good, rels)
    ok, bad = m.check(good, want)
    assert bad == [] and [r for r, _ in ok] == rels
    m.copy(ok, tmp_path / 'out')
    assert (tmp_path / 'out/GLOBAL/GlobalB.lzc').read_bytes() == b'GLOBAL/GlobalB.lzc'
    wrong = tmp_path / 'wrong'
    stand_in(wrong, rels, wrong='GLOBAL/GlobalB.lzc')
    ok, bad = m.check(wrong, want)
    assert bad == ['GLOBAL/GlobalB.lzc']


def test_any_case_and_the_download_before_the_game_folder(tmp_path):
    game = tmp_path / 'game'
    (game / 'FRONTEND').mkdir(parents=True)
    (game / 'FRONTEND/FRONTB.LZC').write_bytes(b'the game folder own copy')
    dl = game / 'Easy Installation-20260924T001502Z-1-001/Easy Installation/Files'
    (dl / 'frontend').mkdir(parents=True)
    (dl / 'frontend/frontb.LZC').write_bytes(b'x')
    roots = m.roots(game)
    assert roots[0] == dl and roots[-1] == game
    assert m.resolve_ci(dl, 'FRONTEND/FrontB.lzc').name == 'frontb.LZC'


def test_everything_else_is_named(tmp_path):
    files = tmp_path / 'Easy Installation/Files'
    for rel in ('FRONTEND/FrontB.lzc', 'SOUND/ENGINE/a.gin', 'scripts/X360Stuff.asi', 'GLOBAL/attributes.bin',
                'GLOBAL/InGameB.lzc', 'FX/COMPILED/x.fx.o', 'TRACKS/STREAML2RA.BUN'):
        (files / rel).parent.mkdir(parents=True, exist_ok=True)
        (files / rel).write_bytes(b'.')
    (files.parent / 'ReadMe.txt').write_bytes(b'.')
    (files.parent / 'ProtonSupport').mkdir()
    (files.parent / 'ProtonSupport/d3d9.dll').write_bytes(b'.')
    names = dict(m.not_used(files, want_of(['FRONTEND/FrontB.lzc'])))
    assert sum(names.values()) == 7   # the ReadMe and the used file are not listed
    assert 'other files' not in names
    assert any('sound' in w for w in names) and any('Windows add-on' in w for w in names)
    assert any('game-data table' in w for w in names) and any('in-race HUD' in w for w in names)


def test_nothing_found_exits_2(tmp_path):
    r = subprocess.run([sys.executable, ROOT / 'tools/setup_kit/x360_stuff.py', tmp_path, tmp_path / 'out'],
                       capture_output=True, text=True)
    assert r.returncode == 2 and 'not added' in r.stdout
    assert not (tmp_path / 'out').exists()


def test_game_side_switch():
    toml = (ROOT / 'mods/core/nfsmw/mod.toml').read_text()
    line = next(l for l in toml.splitlines() if l.startswith('x360_frontend ='))
    assert 'default = true' in line and 'apply = "restart"' in line and 'group = "xbox360"' in line
    c = (ROOT / 'mods/core/nfsmw/nfsmw.c').read_text()
    assert re.search(r'look_pc_boot_off[\s\S]{0,400}"x360_frontend"', c)
    assert 'x360fe_init(api);' in c
    h = (ROOT / 'mods/core/nfsmw/x360_frontend.h').read_text()
    assert 'setting("x360_frontend", 1)' in h and 'overlay_push' in h and '/x360fe' in h
