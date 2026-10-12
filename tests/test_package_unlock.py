"""tools/setup_kit/package.py (2.0.4): game files with the Finder "Locked" flag (uchg). The app's copies are unlocked,
the player's game folder keeps its flags, and an earlier app with locked copies can be removed by a rebuild."""
import importlib.util, os, stat, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('setup_kit_package', ROOT / 'tools/setup_kit/package.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


def locked(p):
    return bool(os.lstat(p).st_flags & stat.UF_IMMUTABLE)


def test_copies_unlocked_game_folder_kept(tmp_path):
    game = tmp_path / 'game'
    (game / 'GLOBAL').mkdir(parents=True)
    for rel in ('server.dll', 'GLOBAL/gameplay.bin'):
        (game / rel).write_bytes(b'x' * 16)
        subprocess.run(['/usr/bin/chflags', 'uchg', str(game / rel)], check=True)
    try:
        out = tmp_path / 'app/Contents/Resources/Game'
        package.clone_tree(game, out, set())
        assert (out / 'GLOBAL/gameplay.bin').read_bytes() == b'x' * 16
        assert not locked(out / 'server.dll') and not locked(out / 'GLOBAL/gameplay.bin')
        assert locked(game / 'server.dll') and locked(game / 'GLOBAL/gameplay.bin')   # never changed
    finally:
        subprocess.run(['/usr/bin/chflags', '-R', 'nouchg', str(game)], check=True)


def test_rebuild_removes_an_app_with_locked_copies(tmp_path):
    app = tmp_path / 'NFS Most Wanted Native.app'
    f = app / 'Contents/Resources/Game/server.dll'
    f.parent.mkdir(parents=True)
    f.write_bytes(b'x')
    subprocess.run(['/usr/bin/chflags', 'uchg', str(f)], check=True)
    package.remove_app(app)
    assert not app.exists()


def test_stuff_pack_download_not_copied(tmp_path):
    game = tmp_path / 'game'
    for d in ('GLOBAL', 'Easy Installation-20260924T001502Z-1-001/Easy Installation/Files', 'X360-Stuff'):
        (game / d).mkdir(parents=True)
    (game / 'GLOBAL/a.bin').write_bytes(b'x')
    out = tmp_path / 'app/Game'
    package.clone_tree(game, out, set())
    assert (out / 'GLOBAL/a.bin').is_file()
    assert not any(n.name.lower().startswith(('easy installation', 'x360-stuff')) for n in out.iterdir())
