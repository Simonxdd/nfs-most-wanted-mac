#!/usr/bin/env python3
"""The Xbox 360 movies (optional): the player's own copy of the US Xbox 360 disc's movies (33 files named
*_english_ntsc.wmv) for the intro, the EA HD bumper and the story scenes. Only the exact original
files are accepted: each one's name, size and SHA-256 must match native/usa_movie_manifest.h, the same list the game
checks before it plays one. Nothing is converted or changed; matching files are copied as they are.

usage: x360_movies.py <folder> <out folder>   copy every verified file into <out folder>; exit 0 when at least one was
                                              copied, 2 when none matched (nothing is written then)
"""
import hashlib, re, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "native/usa_movie_manifest.h"


def manifest(path=MANIFEST):
    """{name: (bytes, sha256)} from the game's own list."""
    text = Path(path).read_text()
    rows = re.findall(r'\{"([a-z0-9_]+_english_ntsc\.wmv)",\s*"([0-9a-f]{64})",\s*(\d+)\}', text)
    return {name: (int(size), digest) for name, digest, size in rows}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verified(folder, want=None):
    """[(name, path)] of the files in folder that match the list exactly (name without regard to case)."""
    want = manifest() if want is None else want
    found = []
    folder = Path(folder)
    if not folder.is_dir():
        return found
    for p in sorted(folder.iterdir()):
        name = p.name.lower()
        if not p.is_file() or name not in want:
            continue
        size, digest = want[name]
        if p.stat().st_size == size and sha256(p) == digest:
            found.append((name, p))
    return found


def copy(files, out):
    out = Path(out)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    for name, p in files:
        # An APFS clone when the disk allows it (no extra space), else a plain copy.
        if subprocess.run(["/bin/cp", "-c", str(p), str(out / name)], capture_output=True).returncode != 0:
            shutil.copyfile(p, out / name)


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 1
    want = manifest()
    files = verified(sys.argv[1], want)
    if not files:
        print(f"Xbox 360 movies: none of the {len(want)} expected files found in {sys.argv[1]}")
        return 2
    copy(files, sys.argv[2])
    missing = sorted(set(want) - {n for n, _ in files})
    print(f"Xbox 360 movies: {len(files)} of {len(want)} added" +
          (f" (missing: {', '.join(missing[:4])}{' ...' if len(missing) > 4 else ''})" if missing else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
