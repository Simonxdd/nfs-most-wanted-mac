#!/usr/bin/env python3
"""The game texture list for the player's Mods folder (2.0.4; kit host/gpu/metal/remaster_mods.h).

Setup runs this on your own game files, on your Mac. It reads every texture pack (tpk.py) and writes hashes and sizes
only, no names:
  k<TAB><hash8><TAB><w><TAB><h>             one row per texture hash and size
  t<TAB><stem hash8><TAB><full hash8>       a name the game stores cut to 23 characters whose hash is of the whole
                                            name: the hash of the 23-character stem -> the texture's hash
The app reads it from Resources/ModNames/known.tsv to say UNKNOWN NAME / WRONG SHAPE in Mods/mods-report.txt and to
match a mod file named by the 23-character stem. Without the list every mod file still loads (nothing is checked).
A file that cannot be read is skipped and counted; the list is still written.

    python mod_names.py <out known.tsv> <game folder> [more folders, e.g. the Xbox 360 front end overlay]
exit 0 = written, 1 = no texture found (nothing written), 2 = usage
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tpk  # noqa: E402

# Folders with no textures, and the player's own download and movie folders beside the game.
SKIP = {'SOUND', 'MOVIES', 'LANGUAGES', 'X360-MOVIES', 'USA-MOVIES', 'X360-STUFF', 'SAVE'}


def bstringhash(s):
    h = 0xFFFFFFFF
    for c in s.encode('latin-1'):
        h = (h * 33 + c) & 0xFFFFFFFF
    return h


def skipped(rel_dirs):
    return any(x.upper() in SKIP or x.upper().startswith('EASY INSTALLATION') for x in rel_dirs)


def pack_files(root):
    """Every .bin/.bun/.lzc under root. Follows links: Setup's original/retail is a folder of links to the game's own
    folders (Path.rglob does not follow them before Python 3.13)."""
    root = Path(root)
    if not root.is_dir():
        return []
    out, seen = [], set()
    for d, dirs, names in os.walk(root, followlinks=True):
        real = os.path.realpath(d)
        rel = Path(d).relative_to(root).parts
        if real in seen or skipped(rel):
            dirs[:] = []
            continue
        seen.add(real)
        out += [Path(d) / n for n in names if Path(n).suffix.lower() in ('.bin', '.bun', '.lzc')]
    return sorted(out)


def build(roots):
    """(sizes, truncated, counts) over every texture pack under the folders."""
    sizes, trunc = set(), set()
    files = n = bad = 0
    for root in roots:
        for f in pack_files(root):
            files += 1
            try:
                found = tpk.textures(str(f))
            except Exception:   # a file the reader does not understand: left out, counted
                bad += 1
                continue
            for t in found:
                h = int(t['hash'], 16)
                sizes.add((h, t['w'], t['h']))
                n += 1
                if len(t['name']) == 23 and bstringhash(t['name']) != h:
                    trunc.add((bstringhash(t['name']), h))
    return sizes, trunc, dict(files=files, textures=n, unreadable=bad)


def write(out, sizes, trunc, c):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    hashes = len({s[0] for s in sizes})
    with open(out, 'w') as o:
        o.write(f"# player-mods game texture list (tools/setup_kit/mod_names.py): {c['files']} files, {c['textures']} "
                f"textures, {hashes} hashes, {len(trunc)} truncated names; hashes and sizes only\n")
        for h, w, ht in sorted(sizes):
            o.write(f'k\t{h:08x}\t{w}\t{ht}\n')
        for a, b in sorted(trunc):
            o.write(f't\t{a:08x}\t{b:08x}\n')
    return hashes


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-2].strip(), file=sys.stderr)
        return 2
    out, roots = Path(argv[0]), [Path(a) for a in argv[1:]]
    sizes, trunc, c = build(roots)
    if not sizes:
        print(f"mods name list: no textures found in {', '.join(map(str, roots))}; the Mods folder works without "
              "the name check")
        return 1
    hashes = write(out, sizes, trunc, c)
    extra = f", {c['unreadable']} files skipped" if c['unreadable'] else ""
    print(f"mods name list: {hashes} game textures from {c['files']} files{extra} (hashes and sizes only)")
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
