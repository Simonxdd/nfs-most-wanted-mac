"""R3 run 29 modding stage 1: the DataMods scan (mods/core/nfsmw/datamods_scan.h), compiled with a small driver and
run on made-up folders: loose vs mod folders, natural A to Z order and "_" parking, SHADOWED, SAVE-RISK (hard and
soft, none on the 2.0.4-proven front-end files), NEW vs OK against a fake game folder, REFUSED and NOT USED, the
report's counts line, a missing folder created, the mod-folder limit."""
import os, subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRIVER = r'''
#include "datamods_scan.h"
int main(int argc, char **argv) {
    static dm_scan s;
    int n = dm_scan_dir(&s, argv[1], argc > 2 ? argv[2] : NULL);
    dm_write_report(&s, argv[1], argc > 2 ? argv[2] : NULL);
    printf("layers %d loose %d\n", n, s.has_loose);
    for (int i = 0; i < s.n_layers; ++i) printf("layer %d %s\n", i, s.layer_name[i]);
    for (int i = 0; i < s.n_files; ++i)
        printf("file %s %s %s\n", k_dm_status[s.files[i].status], s.files[i].layer < 0 ? "." : s.layer_name[s.files[i].layer], s.files[i].rel);
    dm_free(&s);
    return 0;
}
'''


def build(tmp):
    src = Path(tmp) / 'driver.c'
    src.write_text(DRIVER)
    exe = Path(tmp) / 'driver'
    subprocess.run(['cc', '-std=c11', '-D_DEFAULT_SOURCE', '-Wall', '-Werror', '-Wno-unused-function',
                    '-I', str(ROOT / 'mods/core/nfsmw'), str(src), '-o', str(exe)], check=True)
    return exe


def put(p, text='x'):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def run(exe, dm, game):
    out = subprocess.run([str(exe), str(dm), str(game)], capture_output=True, text=True, check=True).stdout
    files = {}
    for l in out.splitlines():
        if l.startswith('file '):
            st_rest = l[5:]
            st = 'SAVE-RISK' if st_rest.startswith('SAVE-RISK') else 'NOT USED' if st_rest.startswith('NOT USED') else st_rest.split()[0]
            rest = st_rest[len(st) + 1:]
            layer, rel = rest.split(' ', 1)
            files[(layer, rel)] = st
    layers = [l.split(' ', 2)[2] for l in out.splitlines() if l.startswith('layer ')]
    return out, layers, files


def test_scan_rules():
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        exe = build(tmp)
        game, dm = t / 'game', t / 'DataMods'
        put(game / 'GLOBAL/attributes.bin'); put(game / 'FRONTEND/FrontB.lzc'); put(game / 'CARS/BMW/GEOMETRY.BIN')
        put(dm / 'GLOBAL/GlobalB.lzc')                      # loose, proven safe -> OK? not in the fake game: NEW
        put(dm / 'mod10/CARS/BMW/GEOMETRY.BIN')             # natural order: mod2 before mod10
        put(dm / 'mod2/CARS/BMW/GEOMETRY.BIN')
        put(dm / 'mod2/GLOBAL/attributes.bin')              # hard save risk
        put(dm / 'mod2/GLOBAL/InGameB.bun')                 # soft save risk
        put(dm / 'mod2/FRONTEND/FrontB.lzc')                # OK (in the game), no risk
        put(dm / 'mod2/scripts/plugin.asi')                 # refused
        put(dm / 'mod2/FX/COMPILED/a.fxo')                  # not used
        put(dm / 'mod2/GLOBAL/evil.EXE')                    # refused inside a game folder
        put(dm / '_parked/CARS/BMW/GEOMETRY.BIN')           # ignored
        put(dm / 'readme.txt')                              # loose top-level file: not used
        out, layers, files = run(exe, dm, game)
        assert layers == ['(loose files)', 'mod2', 'mod10'], out
        assert files[('mod2', 'CARS/BMW/GEOMETRY.BIN')] == 'SHADOWED', out
        assert files[('mod10', 'CARS/BMW/GEOMETRY.BIN')] == 'OK', out
        assert files[('mod2', 'GLOBAL/attributes.bin')] == 'SAVE-RISK', out
        assert files[('mod2', 'GLOBAL/InGameB.bun')] == 'SAVE-RISK', out
        assert files[('mod2', 'FRONTEND/FrontB.lzc')] == 'OK', out
        assert files[('(loose', 'files) GLOBAL/GlobalB.lzc')] == 'NEW', out
        assert files[('mod2', 'scripts/plugin.asi')] == 'REFUSED', out
        assert files[('mod2', 'GLOBAL/evil.EXE')] == 'REFUSED', out
        assert files[('mod2', 'FX/COMPILED/a.fxo')] == 'NOT USED', out
        assert files[('.', 'readme.txt')] == 'NOT USED', out
        assert not any(l == '_parked' for l in layers)
        rep = (dm / 'datamods-report.txt').read_text()
        assert 'Ignored (name starts with _): _parked' in rep
        assert 'Files: 2 OK, 1 new, 2 save-risk, 1 shadowed, 2 refused, 2 not used' in rep, rep


def test_missing_folder_created_and_empty():
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        exe = build(tmp)
        out, layers, files = run(exe, t / 'DataMods', t / 'game')
        assert (t / 'DataMods').is_dir() and layers == [] and files == {}
        assert 'Files: 0 OK' in (t / 'DataMods/datamods-report.txt').read_text()


def test_mod_folder_limit():
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        exe = build(tmp)
        for i in range(34):
            put(t / f'DataMods/m{i:02d}/CARS/x.bin')
        out, layers, files = run(exe, t / 'DataMods', t / 'game')
        assert len(layers) == 32 and layers[-1] == 'm31', out
        assert 'Not loaded: 2 more mod folders' in (t / 'DataMods/datamods-report.txt').read_text()
