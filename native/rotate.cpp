/* rotate.cpp - 006ced90, rotate a range of 8-byte {index, sort key} pairs (the render list's sort helper: the gcd
 * cycle rotate of [first, middle, last)), native (R3 run 31; review/run31/native-design.md B; profile 0.08 / 0.10 /
 * 0.06 ms per frame cruise / burnout / front end).
 *
 * cdecl (first, middle, last), RET. The same steps in the same order as the translation, without the flags of every
 * integer instruction: the gcd of the two lengths by IDIV, then one cycle per gcd step, each element moved with the
 * same two reads and two writes. Left as the original leaves them: EAX, ECX and EDX (from the path taken: the gcd
 * loop, the early exits, or the last cycle), the callee-saved registers restored, the middle argument slot [ESP+8]
 * holding the original's cycle counter. The original's own frame below the caller's ESP (its pushes and two locals)
 * is scratch that nobody reads afterwards and is not written (the culling.c / ftol.cpp precedent). The translation
 * runs when the range overlaps the call's stack words or the IDIV could fault (INT_MIN / -1).
 *
 * RECOMP_NATIVE=0 (all natives) or RECOMP_OPT_NATIVE_ROTATE=0 (this one) run the translation; RECOMP_NATIVE_SELFTEST=1
 * compares both on random ranges (empty halves, one element, in-place cycles) at the first call. */
#include "runtime/guest.h"
extern "C" {
#include "funcs.h"
}
#include "runtime/memory.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>

extern "C" void native_006ced90(X86 *c);

namespace {

int g_native = -1;
void selftest(X86 *c);

int native_on(X86 *c) {
    if (__builtin_expect(g_native >= 0, 1))
        return g_native;
    const char *e = getenv("RECOMP_NATIVE"), *k = getenv("RECOMP_OPT_NATIVE_ROTATE");
    const int want = !(e && e[0] == '0') && !(k && k[0] == '0');
    g_native = 0;
    const char *t = getenv("RECOMP_NATIVE_SELFTEST");
    if (t && t[0] == '1')
        selftest(c);
    g_native = want;
    return g_native;
}

uint32_t sar3(uint32_t v) {
    return (uint32_t)((int32_t)v >> 3);
}

bool rotate(X86 *c) {
    const uint32_t esp0 = c->r[R_ESP];
    if (!gm_valid(esp0, 16))
        return false;
    const uint32_t first = rd32(esp0 + 4), mid = rd32(esp0 + 8), last = rd32(esp0 + 12);
    const uint32_t ebx = last, ebp = first;
    const uint32_t edi = sar3(mid - first);
    uint32_t ecx = sar3(last - first), eax = ecx, esi = edi, edx = c->r[R_EDX];
    // the range must not reach the call's own stack (its frame below ESP and the three argument slots)
    if ((int32_t)ecx > 0 && first < esp0 + 16 && esp0 - 0x18 < first + ecx * 8u)
        return false;
    if (edi != 0) {   // 006cedb7: gcd by IDIV
        do {
            edx = (uint32_t)((int32_t)eax >> 31);
            if ((int32_t)esi == -1 && eax == 0x80000000u)
                return false;   // the original's IDIV faults; the translation keeps that behaviour
            const int32_t r = (int32_t)eax % (int32_t)esi;
            eax = esi;
            edx = (uint32_t)r;
            esi = edx;
        } while (edx != 0);
    }
    bool wrote_arg = false;
    uint32_t arg2 = mid;
    if ((int32_t)eax < (int32_t)ecx && (int32_t)eax > 0) {   // 006cedc2 / 006cedc6
        if (!gm_valid(first, ecx * 8u))
            return false;
        esi = ebp + eax * 8u;
        arg2 = eax;
        wrote_arg = true;
        for (;;) {   // 006cedd2: one cycle
            eax = rd32(esi);
            ecx = rd32(esi + 4);
            const uint32_t l10 = eax, l14 = ecx;
            eax = esi + edi * 8u;
            edx = esi;
            if (eax == ebx)
                eax = ebp;
            while (eax != esi) {   // 006cedf0
                ecx = rd32(eax);
                wr32(edx, ecx);
                ecx = rd32(eax + 4);
                wr32(edx + 4, ecx);
                ecx = sar3(ebx - eax);
                edx = eax;
                if ((int32_t)edi < (int32_t)ecx) {
                    ecx = edi * 8u;
                    eax += ecx;
                } else {
                    eax = ebp + (edi - ecx) * 8u;
                }
            }
            eax = l14;   // 006cee1e
            ecx = l10;
            wr32(edx + 4, eax);
            eax = arg2 - 1u;
            esi -= 8u;
            wr32(edx, ecx);
            arg2 = eax;
            if (eax == 0)
                break;
        }
    }
    if (wrote_arg)
        wr32(esp0 + 8, arg2);
    c->r[R_EAX] = eax;
    c->r[R_ECX] = ecx;
    c->r[R_EDX] = edx;
    c->eip = rd32(esp0);   // the pops restore EBX, EBP, ESI, EDI; RET
    c->r[R_ESP] = esp0 + 4;
    return true;
}

void selftest(X86 *c) {
    const uint32_t stack = c->r[R_ESP] - 0x20000;
    const uint32_t area = stack - 0x4000;   // up to 1024 pairs (8 KB) below the scratch stack
    uint32_t ret = 0x00401001u;
    while (recomp_index_of(ret) >= 0 || recomp_module_lookup(ret) >= 0)
        ++ret;
    static uint32_t init[2048], out_a[2048];
    int bad = 0, runs = 0, fell_back = 0;
    for (int n = 0; n < 3000; ++n) {
        const unsigned len = n < 8 ? (unsigned)n : 1u + (unsigned)rand() % 600u;
        const unsigned m = n < 8 ? (unsigned)n / 2u : (unsigned)rand() % (len + 1u);
        for (unsigned i = 0; i < 2 * len; ++i)
            init[i] = ((uint32_t)rand() << 16) ^ (uint32_t)rand();
        const uint32_t first = area, mid = area + 8u * m, last = area + 8u * len;
        auto setup = [&](X86 &x) {
            for (unsigned i = 0; i < 2 * len; ++i)
                wr32(first + 4u * i, init[i]);
            x = *c;
            x.r[R_ESP] = stack;
            x.r[R_EAX] = 0x0badf00du;
            x.r[R_ECX] = 0x13579bdfu;
            x.r[R_EDX] = 0x2468ace0u;
            x.r[R_EBX] = 0x11111111u;
            x.r[R_EBP] = 0x22222222u;
            x.r[R_ESI] = 0x33333333u;
            x.r[R_EDI] = 0x44444444u;
            wr32(stack, ret);
            wr32(stack + 4, first);
            wr32(stack + 8, mid);
            wr32(stack + 12, last);
        };
        X86 a, b;
        setup(a);
        fn_006ced90(&a);
        for (unsigned i = 0; i < 2 * len; ++i)
            out_a[i] = rd32(first + 4u * i);
        const uint32_t arg_a = rd32(stack + 8);
        setup(b);
        if (!rotate(&b)) {
            ++fell_back;
            fn_006ced90(&b);
        }
        bool same = rd32(stack + 8) == arg_a;
        for (unsigned i = 0; i < 2 * len && same; ++i)
            same = rd32(first + 4u * i) == out_a[i];
        same = same && a.r[R_EAX] == b.r[R_EAX] && a.r[R_ECX] == b.r[R_ECX] && a.r[R_EDX] == b.r[R_EDX] &&
               a.r[R_EBX] == b.r[R_EBX] && a.r[R_EBP] == b.r[R_EBP] && a.r[R_ESI] == b.r[R_ESI] &&
               a.r[R_EDI] == b.r[R_EDI] && a.r[R_ESP] == b.r[R_ESP] && a.eip == b.eip;
        ++runs;
        if (!same && ++bad <= 8)
            fprintf(stderr, "native self-test 006ced90: len %u mid %u differs (eax %08x/%08x ecx %08x/%08x edx %08x/%08x "
                            "arg %08x/%08x)\n", len, m, a.r[R_EAX], b.r[R_EAX], a.r[R_ECX], b.r[R_ECX], a.r[R_EDX],
                    b.r[R_EDX], arg_a, rd32(stack + 8));
    }
    fprintf(stderr, "native self-test 006ced90 (rotate pairs): %d runs, %d differ (%d ran the translation)\n", runs, bad,
            fell_back);
}

} // namespace

extern "C" void native_006ced90(X86 *c) {
    if (!native_on(c) || !rotate(c))
        fn_006ced90(c);
}
