"""The settings schema (mod.toml), NativeOptions.ini, its alias table and the settings guide
must not drift. tools/check_settings.py is the checker."""
from pathlib import Path
import subprocess, sys

def test_settings_schema_has_no_drift():
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, str(root / 'tools/check_settings.py'), str(root)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert 'RESULT PASS' in r.stdout
    assert 'undocumented in both INI and guide: 0:' in r.stdout, r.stdout

def test_antialiasing_is_off_2x_4x():
    """Owner 8 Oct: Smooth edges is Off / 2x / 4x, default Off, the player's choice; the old `msaa` stays declared
    (hidden, -1..1) so a saved value survives until the core mod carries it over (owner 9 Oct: every value -> Off)."""
    import tomllib
    root = Path(__file__).resolve().parents[1]
    s = tomllib.loads((root / 'mods/core/nfsmw/mod.toml').read_text())['settings']
    aa = s['antialiasing']
    assert (aa['type'], aa['default'], aa['min'], aa['max']) == ('int', 0, 0, 2)
    assert aa['choices'].split('|') == ['Off', '2x', '4x']
    assert aa['group'] == 'display.quality' and aa['apply'] == 'renderer'
    old = s['msaa']
    assert (old['default'], old['min'], old['max'], old['group']) == (-1, -1, 1, 'hidden')
    src = (root / 'mods/core/nfsmw/resolution.h').read_text()
    assert 'static int64_t aa_from_old_msaa(int64_t old) { (void)old; return AA_OFF; }' in src   # run 15: all to Off
    assert 'return v == AA_4X && setting("hdr_output", 0) ? AA_2X : v;' in src   # D-116: HDR runs 4x as 2x
    presets = (root / 'mods/core/nfsmw/quality_presets.h').read_text()
    assert '"msaa"' not in presets and '"antialiasing"' not in presets.replace('aa_setting()', '')


def test_retired_settings_are_hidden():
    """Owner 10 Oct (D-117): retired settings are hidden from the menu; run 29 (D-167): no mod source reads them any
    more (their code runs at the value that works), so a saved value changes nothing."""
    import tomllib
    root = Path(__file__).resolve().parents[1]
    s = tomllib.loads((root / 'mods/core/nfsmw/mod.toml').read_text())['settings']
    srcs = ''.join(p.read_text() for p in (root / 'mods/core/nfsmw').glob('*.[ch]'))
    for k in ('grass', 'dof_scene_depth', 'x360_exposure', 'upscale_temporal', 'spray_fill'):
        assert s[k]['group'] == 'hidden', k
        assert f'"{k}"' not in srcs, f'{k} is still read'


def test_help_fits_the_menu():
    """Run 30 (review/run30/settings-ia-design.md): the kit keeps a help text only up to 400 bytes and drops a longer
    one silently (manifest.cpp), so the F10 footer showed nothing for mac_preset and data_mods. Every help must fit."""
    import tomllib
    root = Path(__file__).resolve().parents[1]
    sets = tomllib.loads((root / 'mods/core/nfsmw/mod.toml').read_text())['settings']
    long = {k: len(v.get('help', '').encode()) for k, v in sets.items() if len(v.get('help', '').encode()) > 400}
    assert not long, long
