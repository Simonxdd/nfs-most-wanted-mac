"""3.0.2: mods/core/nfsmw/frontend_pad.h uses the PlayStation prompts only when GLOBAL/XtendedInputButtons.tpk is in
the game folder (the working folder) or a DataMods layer; without it a controller blanked every menu's button bar
(owner, 13 Oct, a 5-hour session on 3.0.1). The two finder functions, compiled with a small driver."""
import os, re, subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def funcs():
    src = (ROOT / 'mods/core/nfsmw/frontend_pad.h').read_text()
    out = []
    for name in ('frontend_pad_find', 'frontend_pad_pack_present'):
        m = re.search(r'^static int ' + name + r'\(.*?^\}\n', src, re.S | re.M)
        assert m, name
        out.append(m.group(0))
    return '\n'.join(out)


DRIVER = '''#include <dirent.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
''' + 'FUNCS' + '''
int main(void) { printf("%d\\n", frontend_pad_pack_present()); return 0; }
'''


def run(game, env):
    with tempfile.TemporaryDirectory() as tmp:
        src, exe = Path(tmp) / 'd.c', Path(tmp) / 'd'
        src.write_text(DRIVER.replace('FUNCS', funcs()))
        subprocess.run(['cc', '-std=gnu11', '-Wall', '-Werror', str(src), '-o', str(exe)], check=True)
        e = {k: v for k, v in os.environ.items() if k not in ('RECOMP_DATAMODS_DIR', 'RECOMP_PROFILE_DIR')}
        e.update(env)
        return subprocess.run([str(exe)], cwd=game, env=e, capture_output=True, text=True, check=True).stdout.strip()


def test_missing_file_keeps_the_pc_prompts(tmp_path):
    (tmp_path / 'game/GLOBAL').mkdir(parents=True)
    (tmp_path / 'game/GLOBAL/GlobalB.lzc').write_bytes(b'x')
    assert run(tmp_path / 'game', {'RECOMP_PROFILE_DIR': str(tmp_path / 'profile')}) == '0'


def test_file_in_the_game_folder_any_case(tmp_path):
    (tmp_path / 'game/global').mkdir(parents=True)
    (tmp_path / 'game/global/xtendedinputbuttons.TPK').write_bytes(b'x')
    assert run(tmp_path / 'game', {}) == '1'


def test_file_in_a_datamods_layer(tmp_path):
    (tmp_path / 'game/GLOBAL').mkdir(parents=True)
    (tmp_path / 'profile/DataMods/buttons/GLOBAL').mkdir(parents=True)
    (tmp_path / 'profile/DataMods/buttons/GLOBAL/XtendedInputButtons.tpk').write_bytes(b'x')
    assert run(tmp_path / 'game', {'RECOMP_PROFILE_DIR': str(tmp_path / 'profile')}) == '1'
    assert run(tmp_path / 'game', {'RECOMP_DATAMODS_DIR': str(tmp_path / 'profile/DataMods')}) == '1'
