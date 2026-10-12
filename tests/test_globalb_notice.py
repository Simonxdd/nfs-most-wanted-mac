"""tools/setup_kit/setup.py global_b_line (2.0.4, run 28 review S3): the Stuff Pack front end overlays GlobalB.lzc,
which also holds car and gameplay tables; a player's own modded GlobalB gets a notice, the original or the Stuff
Pack's own copy (installed into the game folder) does not, and nothing is said without the overlay."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('setup_kit_setup_gb', ROOT / 'tools/setup_kit/setup.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def files(tmp_path, game_bytes, pack_bytes):
    (tmp_path / 'game/GLOBAL').mkdir(parents=True)
    (tmp_path / 'game/GLOBAL/GlobalB.lzc').write_bytes(game_bytes)
    if pack_bytes is not None:
        (tmp_path / 'x360fe/GLOBAL').mkdir(parents=True)
        (tmp_path / 'x360fe/GLOBAL/GlobalB.lzc').write_bytes(pack_bytes)
    return tmp_path / 'game', tmp_path / 'x360fe'


def test_modded_globalb_gets_a_notice(tmp_path):
    line = setup.global_b_line(*files(tmp_path, b'my car mod' * 100, b'stuff pack' * 100))
    assert line and 'changed by a mod (1000 bytes)' in line and 'front end off' in line


def test_the_stuff_packs_own_copy_is_quiet(tmp_path):
    assert setup.global_b_line(*files(tmp_path, b'stuff pack' * 100, b'stuff pack' * 100)) is None


def test_no_overlay_is_quiet(tmp_path):
    assert setup.global_b_line(*files(tmp_path, b'my car mod' * 100, None)) is None


def test_changed_attributes_gets_the_saves_notice(tmp_path):
    (tmp_path / 'game/GLOBAL').mkdir(parents=True)
    (tmp_path / 'game/GLOBAL/attributes.bin').write_bytes(b'stuff pack attributes')
    line = setup.attributes_line(tmp_path / 'game')
    assert line and 'appears to be damaged' in line and 'original GLOBAL folder back' in line


def test_original_attributes_is_quiet():
    vanilla = Path('/Users/damian/Documents/Codex/NFSMW-Remaster/native/native/inputs/game-vanilla')
    if (vanilla / 'GLOBAL/attributes.bin').is_file():
        assert setup.attributes_line(vanilla) is None
