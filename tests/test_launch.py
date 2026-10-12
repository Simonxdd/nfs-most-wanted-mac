"""tools/setup_kit/defaults/Launch (2.0.4): the log starts before anything else, a stop before the game and a game
that quits in its first 20 seconds without the clean exit line are reported (here RECOMP_LAUNCH_DIALOG=0: written to
the log and stderr instead of a dialog), a clean exit is not. Fake apps; no game needed."""
import os, plistlib, shutil, subprocess, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fake_app(tmp_path, game):
    app = tmp_path / 'T.app/Contents'
    (app / 'MacOS').mkdir(parents=True)
    (app / 'Resources/Defaults').mkdir(parents=True)
    (app / 'Resources/Game').mkdir(parents=True)
    shutil.copyfile(ROOT / 'tools/setup_kit/defaults/Launch', app / 'MacOS/Launch')
    (app / 'MacOS/Launch').chmod(0o755)
    for f in ('registry.json', 'mod-settings.json', 'NativeOptions.ini'):
        (app / 'Resources/Defaults' / f).write_text('{}')
    (app / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString': '2.0.4'}))
    if game is not None:
        (app / 'MacOS/SpeedRecomp').write_text('#!/bin/sh\n' + game)
        (app / 'MacOS/SpeedRecomp').chmod(0o755)
    return app / 'MacOS/Launch'


def run(tmp_path, game, wait=2.5):
    prof = tmp_path / 'prof'
    env = dict(os.environ, RECOMP_LAUNCH_DIALOG='0', RECOMP_PROFILE_DIR=str(prof))
    r = subprocess.run([str(fake_app(tmp_path, game))], env=env, capture_output=True, text=True, timeout=30)
    time.sleep(wait)                       # the watcher looks once a second
    logs = sorted((prof / 'Logs').glob('*.log'))
    return r, (logs[-1].read_text() if logs else '')


def test_quick_quit_is_reported(tmp_path):
    r, log = run(tmp_path, 'echo "[recomp] something broke"\nexit 3\n')
    assert r.returncode == 3
    assert log.splitlines()[0].startswith('[launch] ') and 'NFS Most Wanted Native 2.0.4' in log
    assert 'quit right after starting' in log and 'something broke' in log.split('problem:')[1]


def test_clean_exit_is_quiet(tmp_path):
    r, log = run(tmp_path, 'echo "guest exit code:    0"\nexit 0\n')
    assert r.returncode == 0 and 'problem:' not in log and '[launch] starting the game' in log


def test_missing_game_is_reported(tmp_path):
    r, log = run(tmp_path, None, wait=0.5)
    assert r.returncode == 127
    assert 'could not start' in log and 'while starting the game' in log and 'could not start' in r.stderr
