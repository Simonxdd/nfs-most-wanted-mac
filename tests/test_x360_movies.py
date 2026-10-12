"""tools/setup_kit/x360_movies.py: the player's own Xbox 360 movies are accepted only as the exact files of the game's
list (native/usa_movie_manifest.h: name, size, SHA-256). Needs no movie files: a small stand-in list is used for the
matching rules."""
import hashlib, importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('x360_movies', ROOT / 'tools/setup_kit/x360_movies.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_the_list_is_the_games_own():
    want = m.manifest()
    assert len(want) == 33
    assert 'attract_movie_english_ntsc.wmv' in want and 'eahd_bumper_english_ntsc.wmv' in want
    assert all(len(d) == 64 and size > 0 for size, d in want.values())


def test_only_exact_files_are_accepted(tmp_path):
    good, other = b'the right bytes', b'other bytes!!!!'
    want = {'attract_movie_english_ntsc.wmv': (len(good), hashlib.sha256(good).hexdigest()),
            'psa_english_ntsc.wmv': (len(good), hashlib.sha256(good).hexdigest())}
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'ATTRACT_MOVIE_english_ntsc.wmv').write_bytes(good)   # any case
    (src / 'psa_english_ntsc.wmv').write_bytes(other)            # right name and size, wrong content
    (src / 'readme.txt').write_bytes(good)                       # not on the list
    files = m.verified(src, want)
    assert [n for n, _ in files] == ['attract_movie_english_ntsc.wmv']
    m.copy(files, tmp_path / 'out')
    assert sorted(p.name for p in (tmp_path / 'out').iterdir()) == ['attract_movie_english_ntsc.wmv']
    assert (tmp_path / 'out/attract_movie_english_ntsc.wmv').read_bytes() == good


def test_a_missing_folder_gives_nothing(tmp_path):
    assert m.verified(tmp_path / 'absent') == []
