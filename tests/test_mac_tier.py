"""R3 run 29 weaker-Mac presets: mods/core/nfsmw/mac_tier.h's pure functions (chip parse, demand, tier, the read-time
caps), compiled with a small driver. The worked rows of review/run29/presets-design.md section 2 are the cases."""
import subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRIVER = r'''
#define NFSMW_MAC_TIER_PURE 1
#include "mac_tier.h"
int main(int argc, char **argv) {
    if (!strcmp(argv[1], "tier")) {
        int gen = 0, cls = 0;
        unsigned w = (unsigned)atoi(argv[3]), h = (unsigned)atoi(argv[4]);
        if (!mt_parse_chip(argv[2], &gen, &cls)) { printf("unknown\n"); return 0; }
        double d = mt_demand(w, h, gen, cls);
        printf("%d %d %.2f %s\n", gen, cls, d, k_mt_name[mt_pick(d)]);
    } else {   /* apply <tier> <key> <saved> */
        int64_t v = atoll(argv[4]);
        int ch = mt_apply(atoi(argv[2]), argv[3], &v);
        printf("%lld %d\n", (long long)v, ch);
    }
    return 0;
}
'''


def run(*args):
    with tempfile.TemporaryDirectory() as tmp:
        src, exe = Path(tmp) / 'd.c', Path(tmp) / 'd'
        src.write_text(DRIVER)
        subprocess.run(['cc', '-std=c11', '-Wall', '-Werror', '-Wno-unused-function', '-I', str(ROOT / 'mods/core/nfsmw'),
                        str(src), '-o', str(exe)], check=True)
        return [subprocess.run([str(exe), *a], capture_output=True, text=True, check=True).stdout.split() for a in args]


def test_worked_rows():
    rows = [('Apple M5 Pro', 3840, 2160, 'Full'), ('Apple M1 Max', 3840, 2160, 'Full'), ('Apple M3 Max', 3840, 2160, 'Full'),
            ('Apple M1 Pro', 3840, 2160, 'Balanced'), ('Apple M2 Pro', 3840, 2160, 'Balanced'),
            ('Apple M4', 3840, 2160, 'Balanced'), ('Apple M1 Max', 5120, 2880, 'Balanced'), ('Apple M1', 3840, 2160, 'Light'),
            ('Apple M1', 2560, 1440, 'Balanced'), ('Apple M1', 1920, 1080, 'Full')]
    out = run(*[('tier', c, str(w), str(h)) for c, w, h, _ in rows])
    assert [o[-1] for o in out] == [r[3] for r in rows], out


def test_unknown_and_parse():
    out = run(('tier', 'Intel(R) Core(TM) i9', '3840', '2160'), ('tier', '', '3840', '2160'),
              ('tier', 'Apple M12 Ultra', '3840', '2160'))
    assert out[0] == ['unknown'] and out[1] == ['unknown']
    assert out[2][:2] == ['12', '8'] and out[2][-1] == 'Full'


def test_caps_never_raise_cost():
    B, L = '2', '3'
    out = run(('apply', B, 'antialiasing', '2'), ('apply', B, 'antialiasing', '0'), ('apply', B, 'upscale', '2'),
              ('apply', B, 'upscale', '0'), ('apply', L, 'ssao', '1'), ('apply', L, 'upscale', '0'),
              ('apply', '1', 'antialiasing', '2'), ('apply', L, 'hdr_output', '1'), ('apply', L, 'scenery_fade', '1'))
    assert out == [['1', '1'], ['0', '0'], ['2', '0'], ['1', '1'], ['0', '1'], ['2', '1'], ['2', '0'], ['1', '0'], ['1', '0']], out
