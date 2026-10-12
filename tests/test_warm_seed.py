"""tools/warm_seed/warmseed.py (2.0.4): the shader warm-up Setup makes on the player's Mac. No game run here: the
drive list's settings exist, the menu script's grammar, the merge (first-use order, each source kept only with its
file, pipelines deduplicated), the stamp, and the plumbing (setup flag, package option, Launch export only when the
seed folder is there)."""
import importlib.util, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('warmseed', ROOT / 'tools/warm_seed/warmseed.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
K = lambda c: c * 64


def test_drive_settings_are_real_settings():
    keys = {l.split(' =')[0] for l in (ROOT / 'mods/core/nfsmw/mod.toml').read_text().splitlines() if ' = {' in l}
    for name, track, settings, extra in m.DRIVES:
        assert set(settings) <= keys, name
        assert 0 <= track < 30
        assert not (settings.get('antialiasing') == 2 and extra.get('RECOMP_TEST_HDR_FLOAT')), 'HDR never with 4x'
    assert len({n for n, *_ in m.DRIVES}) == len(m.DRIVES)


def test_script_grammar():
    s = m.script(3, 1000)
    for line in s.splitlines():
        assert re.fullmatch(r'wait \d+|key (RETURN|RIGHT) (down|up)|quit', line), line
    assert s.count('key RIGHT down') == 3 + 3 and s.rstrip().endswith('quit')


def test_merge(tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    a.mkdir(); b.mkdir()
    (a / 'order.txt').write_text(f'{K("1")}\n{K("2")}\nnot-a-key\n')
    (a / f'{K("1")}.msl').write_text('one')
    (a / f'{K("2")}.msl').write_text('two')
    (a / 'pipelines.txt').write_text('p1 x\np1 y\n')
    (b / 'order.txt').write_text(f'{K("2")}\n{K("3")}\n{K("4")}\n')
    (b / f'{K("2")}.msl').write_text('two again')
    (b / f'{K("3")}.msl').write_text('three')          # K4 has no file: left out
    (b / 'pipelines.txt').write_text('p1 y\np1 z\n')
    libs, pipes = m.merge([a, b], tmp_path / 'seed')
    seed = tmp_path / 'seed'
    assert (libs, pipes) == (3, 3)
    assert (seed / 'order.txt').read_text().split() == [K('1'), K('2'), K('3')]
    assert (seed / f'{K("2")}.msl').read_text() == 'two'
    assert (seed / 'pipelines.txt').read_text().split('\n')[:3] == ['p1 x', 'p1 y', 'p1 z']
    stamp = (seed / 'stamp.txt').read_text().splitlines()
    assert stamp[0] == 'fingerprint ' + m.fingerprint() and stamp[3] == 'libraries 3 pipelines 3'
    assert not (tmp_path / 'seed.staging').exists()


def test_plumbing():
    setup = (ROOT / 'tools/setup_kit/setup.py').read_text()
    assert '--no-shader-seed' in setup and 'tools/warm_seed/warmseed.py' in setup and '"--warm-seed", seed' in setup
    assert '--warm-seed' in (ROOT / 'tools/setup_kit/package.py').read_text()
    launch = (ROOT / 'tools/setup_kit/defaults/Launch').read_text()
    assert re.search(r'if \[ -f "\$app_resources/ShaderWarmSeed/order.txt" \]; then\n\s+export RECOMP_D3D9_SHADER_WARM_SEED=', launch)
    assert 'unset RECOMP_D3D9_SHADER_WARM_SEED' in launch
    swift = (ROOT / 'tools/installer/SetupApp.swift').read_text()
    assert '"--no-shader-seed"' in swift and 'shader warm-up: drive ' in swift
