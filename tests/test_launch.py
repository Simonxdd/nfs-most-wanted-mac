"""tools/setup_kit/defaults/Launch (2.0.4): the log starts before anything else, a stop before the game and a game
that quits in its first 20 seconds without the clean exit line are reported (here RECOMP_LAUNCH_DIALOG=0: written to
the log and stderr instead of a dialog), a clean exit is not. Fake apps; no game needed."""
import os, plistlib, shutil, subprocess, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fake_app(tmp_path, game):
    app = tmp_path / 'T.app/Contents'
    (app / 'MacOS').mkdir(parents=True, exist_ok=True)
    (app / 'Resources/Defaults').mkdir(parents=True, exist_ok=True)
    (app / 'Resources/Game').mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / 'tools/setup_kit/defaults/Launch', app / 'MacOS/Launch')
    (app / 'MacOS/Launch').chmod(0o755)
    for f in ('registry.json', 'mod-settings.json', 'NativeOptions.ini'):
        (app / 'Resources/Defaults' / f).write_text('{}')
    if not (app / 'Info.plist').exists():   # the same build when run again (the backup keys on it)
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
    time.sleep(2)                          # the watcher must not add a second report (review S1)
    assert log.count('problem:') == 1 == sorted((tmp_path / 'prof/Logs').glob('*.log'))[-1].read_text().count('problem:')


def test_saves_backed_up_once_per_build(tmp_path):
    prof = tmp_path / 'prof'
    save = prof / 'Documents/NFS Most Wanted/NAME/NAME'
    save.parent.mkdir(parents=True)
    save.write_bytes(b'career')
    game = 'echo "guest exit code:    0"\nexit 0\n'
    run(tmp_path, game, wait=0.5)
    backups = [d for d in (prof / 'Save Backups').iterdir() if d.is_dir()]
    assert len(backups) == 1 and backups[0].name.endswith('before 2.0.4')
    assert (backups[0] / 'Documents/NFS Most Wanted/NAME/NAME').read_bytes() == b'career'
    r, log = run(tmp_path, game, wait=0.5)      # same build again: no second copy
    assert len([d for d in (prof / 'Save Backups').iterdir() if d.is_dir()]) == 1


def test_no_saves_no_backup(tmp_path):
    r, log = run(tmp_path, 'echo "guest exit code:    0"\nexit 0\n', wait=0.5)
    assert not (tmp_path / 'prof/Save Backups').exists() and r.returncode == 0


def test_a_run_past_20_seconds_is_quiet(tmp_path):
    """The watcher reads the clock (run 28: counting its own one-second sleeps undercounted when macOS slowed it)."""
    r, log = run(tmp_path, 'sleep 24\necho "[recomp] ended without the clean exit line"\nexit 1\n', wait=2.5)
    assert 'problem:' not in log


def test_the_players_own_quit_is_quiet(tmp_path):
    """Cmd-Q or the close button: the host logs it, and the watcher says nothing (run 28)."""
    for line in ('[host] quit requested: asking the game to close', '[host] window close requested: asking the game to close'):
        sub = tmp_path / line[7:11]
        sub.mkdir()
        r, log = run(sub, f'echo "{line}"\necho "[host] guest did not act on WM_CLOSE within 15s; unwound by the host"\nexit 0\n')
        assert 'problem:' not in log, line
