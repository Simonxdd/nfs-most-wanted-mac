"""3.0.1: the Setup app ticks Make HD textures / Prepare the shaders when an earlier Setup made them (a finished HD
pack in the cache, a warm-up seed in the kept build), so an update does not build an app without them; the time
estimate counts that work as reused. tools/installer/SetupApp.swift's earlierWork and firstBuildEstimate, compiled
with a small driver."""
import re, subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''
import Foundation
let a = CommandLine.arguments
let w = earlierWork(support: URL(fileURLWithPath: a[1]), work: URL(fileURLWithPath: a[2]))
print(w.hd, w.seed, firstBuildEstimate(hd: w.hd, shaders: w.seed, hdMade: w.hd, seedMade: w.seed),
      firstBuildEstimate(hd: true, shaders: true))
'''


def funcs():
    src = (ROOT / 'tools/installer/SetupApp.swift').read_text()
    out = []
    for name in ('earlierWork', 'startingTicks', 'firstBuildEstimate'):
        m = re.search(r'^func ' + name + r'\(.*?^\}\n', src, re.S | re.M)
        assert m, name
        out.append(m.group(0))
    return '\n'.join(out)


def test_earlier_work_ticks_and_estimate():
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / 'main.swift').write_text(funcs() + DRIVER)
        subprocess.run(['swiftc', '-o', str(t / 'd'), str(t / 'main.swift')], check=True, capture_output=True)
        sup, work = t / 'support', t / 'work'

        def run():
            return subprocess.run([str(t / 'd'), str(sup), str(work)], capture_output=True, text=True,
                                  check=True).stdout.split(maxsplit=2)

        assert run()[:2] == ['false', 'false']
        (sup / 'hd/pack').mkdir(parents=True)
        (sup / 'hd/pack/index.tsv').write_text('k\n')
        assert run()[:2] == ['true', 'false']
        (work / 'build/warm/seed').mkdir(parents=True)
        (work / 'build/warm/seed/order.txt').write_text('k\n')
        hd, seed, rest = run()
        assert (hd, seed) == ('true', 'true')
        assert rest.split('\n')[0].strip() == '55 minutes 2 hours'   # HD reused, shaders again: 10 + 10 + 35; new: 10 + 75 + 35


def test_app_sets_the_ticks_from_earlier_work():
    swift = (ROOT / 'tools/installer/SetupApp.swift').read_text()
    assert 'let madeBefore = earlierWork(support: supportDir, work: workDir)' in swift
    assert 'startingTicks(saved: saved, found: madeBefore)' in swift
    assert 'forKey: "builtHdTextures")' in swift and 'forKey: "builtShaderSeed")' in swift


def test_a_saved_choice_wins_over_earlier_work():
    """Review 3.0.1 item 5: a player who left the HD textures out keeps them out on the next update."""
    drv = "import Foundation\n" + funcs() + """
let t = [startingTicks(saved: (hd: nil, seed: nil), found: (hd: true, seed: true)),
         startingTicks(saved: (hd: false, seed: nil), found: (hd: true, seed: true)),
         startingTicks(saved: (hd: true, seed: false), found: (hd: false, seed: true))]
print(t.map { "\\($0.hd) \\($0.seed)" }.joined(separator: ","))
"""
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / 'main.swift').write_text(drv)
        subprocess.run(['swiftc', '-o', str(t / 'd'), str(t / 'main.swift')], check=True, capture_output=True)
        out = subprocess.run([str(t / 'd')], capture_output=True, text=True, check=True).stdout.strip()
    assert out == 'true true,false true,true false', out
