/* texreplace.cpp - 00504f70, eModel::ApplyReplacementTextureTable, native (R3 run 32; review/run31/native-design.md C;
 * profile 0.07 ms per frame cruise).
 *
 * thiscall (ECX model; [ESP+4] restore array), RET 4. For each replacement entry {?, hash-flag word, TextureInfo*}
 * (12 bytes, table at model+0x10, count at model+0x14) the restore slot is cleared; then for each of the solid's
 * texture slots (solid at model+0xc, count = signed byte solid+0x19, slot table at solid+0x2c, 8 bytes {hash,
 * TextureInfo*}) the first entry with a non-zero +4 word and the slot's hash swaps its pointer with the slot's and the
 * slot's address goes in the restore array at the entry's index. The same reads and writes in the same order as the
 * translation (the slot table pointer re-read where the original re-reads it), without the flags of every integer
 * instruction. Left as the original leaves them: EAX = the slot count, ECX and EDX from the path taken, the callee-
 * saved registers restored, the flags of the closing ADD ESP,0xc. The original's frame below the caller's ESP (its
 * pushes and three locals) is scratch nobody reads afterwards and is not written (the culling.c / ftol.cpp precedent).
 * The translation runs when an entry is still unresolved (pointer -1: the original calls 00503400 to look the texture
 * up; that happens once per model, the result is stored), or any table reaches the call's own stack words.
 *
 * RECOMP_NATIVE=0 (all natives) or RECOMP_OPT_NATIVE_TEXREPLACE=0 (this one) run the translation;
 * RECOMP_NATIVE_SELFTEST=1 compares both on random models (hash overlaps, zero flag words, empty tables, a restore
 * array inside the replacement table) at the first call. */
#include "runtime/guest.h"
extern "C" {
#include "funcs.h"
}
#include "runtime/memory.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>

extern "C" void native_00504f70(X86 *c);

namespace {

int g_native = -1;
void selftest(X86 *c);

int native_on(X86 *c) {
    if (__builtin_expect(g_native >= 0, 1))
        return g_native;
    const char *e = getenv("RECOMP_NATIVE"), *k = getenv("RECOMP_OPT_NATIVE_TEXREPLACE");
    const int want = !(e && e[0] == '0') && !(k && k[0] == '0');
    g_native = 0;
    const char *t = getenv("RECOMP_NATIVE_SELFTEST");
    if (t && t[0] == '1')
        selftest(c);
    g_native = want;
    return g_native;
}

bool overlap(uint32_t a, uint32_t an, uint32_t b, uint32_t bn) {
    return an && bn && a < b + bn && b < a + an;
}

void add_flags(X86 *c, uint32_t a, uint32_t b) {   // the closing ADD ESP,0xc
    const uint64_t rf = (uint64_t)a + b;
    const uint32_t r = (uint32_t)rf;
    c->eflags_cf = (uint32_t)((rf >> 32) & 1u);
    c->eflags_of = ((~(a ^ b) & (a ^ r)) >> 31) & 1u;
    c->eflags_af = ((a ^ b ^ r) >> 4) & 1u;
    c->eflags_zf = r == 0;
    c->eflags_sf = r >> 31;
    c->eflags_pf = parity8(r);
}

bool apply(X86 *c) {
    const uint32_t esp0 = c->r[R_ESP];
    if (!gm_valid(esp0 - 0x1c, 0x24))
        return false;
    const uint32_t model = c->r[R_ECX];
    if (!gm_valid(model, 0x18))
        return false;
    const uint32_t count = rd32(model + 0x14), solid = rd32(model + 0xc), table = rd32(model + 0x10);
    if (!gm_valid(solid, 0x30))
        return false;
    const uint32_t slots = (uint32_t)(int32_t)(int8_t)rd8(solid + 0x19);
    uint32_t ecx = model, edx = c->r[R_EDX];
    if (table != 0 && count != 0) {
        const uint32_t restore = rd32(esp0 + 4), frame = esp0 - 0x1c;
        const int32_t n = (int32_t)count, ns = (int32_t)slots;
        if (overlap(model, 0x18, frame, 0x24) || overlap(solid, 0x30, frame, 0x24))
            return false;   // the original's pushes and locals would land on them before they are read
        // every check before the first write: a fallback must leave nothing written (the translation reruns)
        const uint32_t st = rd32(solid + 0x2c);
        if (ns > 0 && (!gm_valid(st, 8u * slots) || overlap(st, 8u * slots, frame, 0x24) ||
                       overlap(st + 4u, 8u * slots - 4u, solid, 0x30)))
            return false;
        if (n > 0) {
            if (n > 0x10000 || !gm_valid(table, 12u * count) || !gm_valid(restore, 4u * count) ||
                overlap(table, 12u * count, frame, 0x24) || overlap(restore, 4u * count, frame, 0x24) ||
                overlap(table, 12u * count, solid, 0x30) || overlap(restore, 4u * count, solid, 0x30))
                return false;
            for (uint32_t i = 0; i < count; ++i)   // the original calls 00503400 for an unresolved entry (a cleared
                if (rd32(table + 8u + 12u * i) == 0xffffffffu)   // restore slot writes 0, never -1)
                    return false;
            for (uint32_t i = 0; i < count; ++i)
                wr32(restore + 4u * i, 0);
            ecx = restore;
        }
        if (ns > 0) {
            for (uint32_t s = 0; s < slots; ++s) {
                const uint32_t hash = rd32(rd32(solid + 0x2c) + 8u * s);   // 00504fe4
                edx = hash;
                ecx = 0;
                if (n <= 0)
                    continue;
                uint32_t e = table;
                for (; (int32_t)ecx < n; ++ecx, e += 12u) {
                    if (rd32(e + 4) == 0 || rd32(e) != hash)
                        continue;
                    const uint32_t st2 = rd32(solid + 0x2c);   // 00505009
                    const uint32_t old = rd32(st2 + 8u * s + 4u);
                    wr32(st2 + 8u * s + 4u, rd32(e + 8));
                    edx = rd32(solid + 0x2c) + 8u * s + 4u;
                    wr32(restore + 4u * ecx, edx);
                    wr32(e + 8, old);
                    break;
                }
            }
        }
        c->r[R_EAX] = slots;
        c->r[R_ECX] = ecx;
        c->r[R_EDX] = edx;
    } else {
        c->r[R_EAX] = slots;   // 0050503d: ECX and EDX as they came in
    }
    add_flags(c, esp0 - 0xc, 0xc);
    c->eip = rd32(esp0);   // RET 4 (EBX, EBP, ESI and EDI restored by the pops)
    c->r[R_ESP] = esp0 + 8;
    return true;
}

void selftest(X86 *c) {
    const uint32_t stack = c->r[R_ESP] - 0x20000;
    const uint32_t area = stack - 0x8000;   // model, solid, slot table, replacement table, restore array
    uint32_t ret = 0x00401001u;
    while (recomp_index_of(ret) >= 0 || recomp_module_lookup(ret) >= 0)
        ++ret;
    static uint8_t init[0x7000], out_a[0x7000];
    int bad = 0, runs = 0, fell_back = 0;
    for (int n = 0; n < 4000; ++n) {
        for (unsigned i = 0; i < sizeof init; ++i)
            init[i] = (uint8_t)rand();
        const uint32_t model = area, solid = area + 0x40, st = area + 0x100, table = area + 0x600,
                       restore = (n % 37 == 5) ? table + 4u * (rand() % 8) : area + 0x1800;
        const unsigned count = n % 29 == 3 ? 0u : (unsigned)rand() % 24u, slots = (unsigned)rand() % 40u;
        auto w32 = [&](uint32_t a, uint32_t v) { memcpy(init + (a - area), &v, 4); };
        w32(model + 0xc, solid);
        w32(model + 0x10, n % 31 == 7 ? 0u : table);
        w32(model + 0x14, count);
        init[solid + 0x19 - area] = (uint8_t)(n % 41 == 9 ? 0x80u + (unsigned)rand() % 0x80u : slots);
        w32(solid + 0x2c, st);
        const uint32_t hashes[6] = {0x1234u, 0xbeefu, 0u, 0xffffffffu, 0x55aa55aau, 0x7u};
        for (unsigned s = 0; s < 40; ++s)
            w32(st + 8u * s, hashes[rand() % 6]);
        for (unsigned i = 0; i < 24; ++i) {
            w32(table + 12u * i, hashes[rand() % 6]);
            if (rand() % 4 == 0)
                w32(table + 12u * i + 4u, 0);
            uint32_t p;
            memcpy(&p, init + (table + 12u * i + 8u - area), 4);
            if (p == 0xffffffffu)
                w32(table + 12u * i + 8u, 0x1000u + i);
        }
        if (restore != area + 0x1800)   // keep the aliased restore array from writing -1 into a pointer word
            for (unsigned i = 0; i < 24; ++i)
                w32(table + 12u * i + 8u, 0x2000u + i);
        auto setup = [&](X86 &x) {
            for (unsigned i = 0; i < sizeof init; i += 4) {
                uint32_t v;
                memcpy(&v, init + i, 4);
                wr32(area + i, v);
            }
            x = *c;
            x.r[R_ESP] = stack;
            x.r[R_EAX] = 0x0badf00du;
            x.r[R_ECX] = model;
            x.r[R_EDX] = 0x2468ace0u;
            x.r[R_EBX] = 0x11111111u;
            x.r[R_EBP] = 0x22222222u;
            x.r[R_ESI] = 0x33333333u;
            x.r[R_EDI] = 0x44444444u;
            wr32(stack, ret);
            wr32(stack + 4, restore);
        };
        X86 a, b;
        setup(a);
        fn_00504f70(&a);
        for (unsigned i = 0; i < sizeof init; i += 4) {
            const uint32_t v = rd32(area + i);
            memcpy(out_a + i, &v, 4);
        }
        setup(b);
        if (!apply(&b)) {
            ++fell_back;
            setup(b);
            fn_00504f70(&b);
        }
        bool same = true;
        for (unsigned i = 0; i < sizeof init && same; i += 4) {
            uint32_t v;
            memcpy(&v, out_a + i, 4);
            same = rd32(area + i) == v;
        }
        same = same && a.r[R_EAX] == b.r[R_EAX] && a.r[R_ECX] == b.r[R_ECX] && a.r[R_EDX] == b.r[R_EDX] &&
               a.r[R_EBX] == b.r[R_EBX] && a.r[R_EBP] == b.r[R_EBP] && a.r[R_ESI] == b.r[R_ESI] &&
               a.r[R_EDI] == b.r[R_EDI] && a.r[R_ESP] == b.r[R_ESP] && a.eip == b.eip &&
               x86_get_eflags(&a) == x86_get_eflags(&b);
        ++runs;
        if (!same && ++bad <= 8)
            fprintf(stderr, "native self-test 00504f70: count %u slots %u differs (eax %08x/%08x ecx %08x/%08x "
                            "edx %08x/%08x)\n", count, slots, a.r[R_EAX], b.r[R_EAX], a.r[R_ECX], b.r[R_ECX],
                    a.r[R_EDX], b.r[R_EDX]);
    }
    fprintf(stderr, "native self-test 00504f70 (texture replacement): %d runs, %d differ (%d ran the translation)\n",
            runs, bad, fell_back);
}

} // namespace

extern "C" void native_00504f70(X86 *c) {
    if (!native_on(c) || !apply(c))
        fn_00504f70(c);
}
