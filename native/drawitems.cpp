/* drawitems.cpp - 006da000, copy 12-byte draw items {model|flag, matrix, instance} (std::uninitialized_copy over the
 * renderer's item list), native (R3 run 31; review/run31/native-design.md E).
 *
 * cdecl (first, last, dest), RET. Each item's three words are read and written in the original's order (so an
 * overlapping copy behaves as the original's does). Left as the original leaves them: EAX = dest + the bytes copied,
 * ECX = EDX = last (EAX = dest, ECX = first, EDX = last when the range is empty); EBX, ESI, EDI pushed and popped.
 * The translation runs for a null destination (the original skips null slots and keeps walking from address 0), a
 * range that is not whole items (the original would never stop), or ranges that reach the call's stack words.
 *
 * RECOMP_NATIVE=0 (all natives) or RECOMP_OPT_NATIVE_DRAWITEMS=0 (this one) run the translation;
 * RECOMP_NATIVE_SELFTEST=1 compares both on random item lists (overlapping copies included) at the first call. */
#include "runtime/guest.h"
extern "C" {
#include "funcs.h"
}
#include "runtime/memory.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>

extern "C" void native_006da000(X86 *c);

namespace {

int g_native = -1;
void selftest(X86 *c);

int native_on(X86 *c) {
    if (__builtin_expect(g_native >= 0, 1))
        return g_native;
    const char *e = getenv("RECOMP_NATIVE"), *k = getenv("RECOMP_OPT_NATIVE_DRAWITEMS");
    const int want = !(e && e[0] == '0') && !(k && k[0] == '0');
    g_native = 0;
    const char *t = getenv("RECOMP_NATIVE_SELFTEST");
    if (t && t[0] == '1')
        selftest(c);
    g_native = want;
    return g_native;
}

bool overlap(uint32_t a, uint32_t an, uint32_t b, uint32_t bn) {
    return a < b + bn && b < a + an;
}

bool copy_items(X86 *c) {
    const uint32_t esp0 = c->r[R_ESP];
    if (!gm_valid(esp0, 16))
        return false;
    uint32_t ecx = rd32(esp0 + 4);
    const uint32_t edx = rd32(esp0 + 8);
    uint32_t eax = rd32(esp0 + 12);
    if (ecx != edx) {
        const uint32_t bytes = edx - ecx;
        if (bytes % 12u != 0 || bytes > 0x10000000u || eax == 0 || !gm_valid(ecx, bytes) || !gm_valid(eax, bytes))
            return false;
        if (overlap(ecx, bytes, esp0 - 12, 28) || overlap(eax, bytes, esp0 - 12, 28))
            return false;
        do {
            uint32_t w = rd32(ecx);
            wr32(eax, w);
            w = rd32(ecx + 4);
            wr32(eax + 4, w);
            w = rd32(ecx + 8);
            wr32(eax + 8, w);
            ecx += 12u;
            eax += 12u;
        } while (ecx != edx);
    }
    c->r[R_EAX] = eax;
    c->r[R_ECX] = ecx;
    c->r[R_EDX] = edx;
    c->eip = rd32(esp0);   // RET (EBX, ESI, EDI restored by the pops)
    c->r[R_ESP] = esp0 + 4;
    return true;
}

void selftest(X86 *c) {
    const uint32_t stack = c->r[R_ESP] - 0x20000;
    const uint32_t area = stack - 0x8000;   // source and destination lists below the scratch stack
    uint32_t ret = 0x00401001u;
    while (recomp_index_of(ret) >= 0 || recomp_module_lookup(ret) >= 0)
        ++ret;
    static uint32_t init[4096], out_a[4096];
    const unsigned span = 4096;   // words of the whole test area (16 KB: both lists at their farthest)
    int bad = 0, runs = 0, fell_back = 0;
    for (int n = 0; n < 3000; ++n) {
        const unsigned items = n < 4 ? (unsigned)n : (unsigned)rand() % 300u;
        const uint32_t first = area + 4u * ((unsigned)rand() % 64u);
        // destination: far away, or overlapping the source either way (every 10th / 11th run)
        int32_t shift = (n % 10 == 0) ? 12 * ((rand() % 5) + 1) : (n % 11 == 0) ? -12 * ((rand() % 5) + 1) : 0x1800;
        const uint32_t dest = first + (uint32_t)shift;
        for (unsigned i = 0; i < span; ++i)
            init[i] = ((uint32_t)rand() << 16) ^ (uint32_t)rand();
        auto setup = [&](X86 &x) {
            for (unsigned i = 0; i < span; ++i)
                wr32(area - 0x100 + 4u * i, init[i]);
            x = *c;
            x.r[R_ESP] = stack;
            x.r[R_EAX] = 0x0badf00du;
            x.r[R_ECX] = 0x13579bdfu;
            x.r[R_EDX] = 0x2468ace0u;
            x.r[R_EBX] = 0x11111111u;
            x.r[R_ESI] = 0x33333333u;
            x.r[R_EDI] = 0x44444444u;
            wr32(stack, ret);
            wr32(stack + 4, first);
            wr32(stack + 8, first + 12u * items);
            wr32(stack + 12, dest);
        };
        X86 a, b;
        setup(a);
        fn_006da000(&a);
        for (unsigned i = 0; i < span; ++i)
            out_a[i] = rd32(area - 0x100 + 4u * i);
        setup(b);
        if (!copy_items(&b)) {
            ++fell_back;
            fn_006da000(&b);
        }
        bool same = true;
        for (unsigned i = 0; i < span && same; ++i)
            same = rd32(area - 0x100 + 4u * i) == out_a[i];
        same = same && a.r[R_EAX] == b.r[R_EAX] && a.r[R_ECX] == b.r[R_ECX] && a.r[R_EDX] == b.r[R_EDX] &&
               a.r[R_EBX] == b.r[R_EBX] && a.r[R_ESI] == b.r[R_ESI] && a.r[R_EDI] == b.r[R_EDI] &&
               a.r[R_EBP] == b.r[R_EBP] && a.r[R_ESP] == b.r[R_ESP] && a.eip == b.eip;
        ++runs;
        if (!same && ++bad <= 8)
            fprintf(stderr, "native self-test 006da000: %u items shift %d differs (eax %08x/%08x ecx %08x/%08x)\n", items,
                    shift, a.r[R_EAX], b.r[R_EAX], a.r[R_ECX], b.r[R_ECX]);
    }
    fprintf(stderr, "native self-test 006da000 (copy draw items): %d runs, %d differ (%d ran the translation)\n", runs,
            bad, fell_back);
}

} // namespace

extern "C" void native_006da000(X86 *c) {
    if (!native_on(c) || !copy_items(c))
        fn_006da000(c);
}
