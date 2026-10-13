#!/usr/bin/env python3
"""The HD HUD: a sharp race HUD, minimap icons and fonts at 4x, made on this Mac from your own game files.

    hud_pack.py --game DIR [--stuff DIR] --out DIR [--tiles] [--cache DIR]

Run by the setup ("Make the HD HUD", on by default); it can also run on its own. Two layers, one step:
  <out>/ui   the picture layer (ui_pack.py): the game's HUD textures at 4x with their outlines redrawn;
  <out>/ui2  the drawing layer (ui2_pack.py): 40 of them redrawn from our own vector art (hud_svg/), read over ui.
Each layer writes its index.tsv last, only when finished; if the drawing layer fails, the picture layer stays and
this says so. --tiles adds the minimap tiles to the picture layer (about 1.8 GB, off by default).
Tools: numpy, Pillow and etcpak (pinned in requirements.txt). When this Python lacks them, the HD textures'
environment is used when it exists, else a small one is made in the cache (about 60 MB, once; the only download).
No model. Nothing in the game folder is changed. Progress lines start with "HUD textures:".
Exit 0 when at least the picture layer is finished, 1 otherwise."""
import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREFIX = 'HUD textures: '
NEEDS = ('numpy', 'pillow', 'etcpak')


def say(msg):
    print(PREFIX + msg, flush=True)


def have_tools():
    return all(importlib.util.find_spec(m) for m in ('numpy', 'PIL', 'etcpak'))


def pins():
    """The numpy, pillow and etcpak lines of requirements.txt."""
    out = []
    for line in (HERE / 'requirements.txt').read_text().splitlines():
        name = line.split('==')[0].strip().lower()
        if name in NEEDS and '==' in line:
            out.append(line.strip())
    return out


def works(py):
    return Path(py).is_file() and subprocess.run([str(py), '-c', 'import numpy, PIL, etcpak'],
                                                 capture_output=True).returncode == 0


def hd_env():
    """The HD textures' environment (hdpack.py), when it is ready."""
    try:
        sys.path.insert(0, str(HERE))
        import hdpack
        venv = hdpack.cache_root() / 'venv'
    except Exception:
        return None
    py = venv / 'bin/python'
    return py if (venv / '.ready').is_file() and works(py) else None


def own_env(cache):
    venv = Path(cache) / 'venv'
    py, stamp = venv / 'bin/python', venv / '.ready'
    want = '\n'.join(pins()) + '\n'
    if stamp.is_file() and stamp.read_text() == want and works(py):
        return py
    say("installing the HD HUD's tools (numpy, Pillow, etcpak; about 60 MB, once)")
    venv.parent.mkdir(parents=True, exist_ok=True)
    if not py.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
    with open(Path(cache) / 'pip.log', 'w') as log:
        r = subprocess.run([str(py), '-m', 'pip', 'install', '--disable-pip-version-check', '--retries', '8',
                            '--timeout', '30', '--only-binary=:all:'] + pins(), stdout=log, stderr=subprocess.STDOUT)
    if r.returncode or not works(py):
        raise SystemExit(f"the HD HUD's tools could not be installed (see {Path(cache) / 'pip.log'})")
    stamp.write_text(want)
    return py


def run_layers(a, cache):
    import ui_pack
    import ui2_pack
    ui, ui2 = a.out / 'ui', a.out / 'ui2'
    args = ['--game', str(a.game), '--out', str(ui)] + (['--stuff', str(a.stuff)] if a.stuff else [])
    if ui_pack.main(args + (['--tiles'] if a.tiles else [])):
        say('HD HUD: not made this time')
        return 1
    if ui2_pack.main(['--ui', str(ui), '--out', str(ui2), '--cache', str(cache)]):
        say('HD HUD: the drawing layer was not made; the picture layer is used on its own')
        return 0
    say('HD HUD: done (picture layer + drawing layer)')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--game', type=Path, required=True, help='your game folder (read only)')
    ap.add_argument('--stuff', type=Path, help='a folder holding the Xbox 360 Stuff Pack download (optional)')
    ap.add_argument('--out', type=Path, required=True, help='the output folder (gets ui/ and ui2/)')
    ap.add_argument('--tiles', action='store_true', help='also the minimap tiles (about 1.8 GB; off by default)')
    ap.add_argument('--cache', type=Path, help='tools and the renderer (default: --out)')
    ap.add_argument('--in-env', action='store_true', help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    cache = a.cache or a.out
    try:
        if have_tools():
            sys.path.insert(0, str(HERE))
            return run_layers(a, cache)
        if a.in_env:
            raise SystemExit('numpy, Pillow or etcpak missing in the tools environment')
        py = hd_env() or own_env(cache)
        cmd = [str(py), str(HERE / 'hud_pack.py'), '--in-env', '--game', str(a.game), '--out', str(a.out),
               '--cache', str(cache)] + (['--stuff', str(a.stuff)] if a.stuff else []) + (['--tiles'] if a.tiles else [])
        return subprocess.run(cmd).returncode
    except SystemExit as e:
        if e.code not in (None, 0):
            say(f'HD HUD stopped: {e.code}')
        return 1
    except Exception as e:   # never a traceback in the player's setup window
        say(f'HD HUD stopped: {str(e)[:160]}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
