/* ftol.cpp - the C runtime's float-to-integer helper (007c4b80, MSVC's _ftol2 shape), native.
 *
 * R3 run 29 optimisation pass O3 (review/run29/opt-design.md; D-166/D-167). The game converts floats to integers
 * through this helper everywhere; translated, each call pushes a frame, writes and reads four scratch words on the
 * guest stack and runs about 20 instructions of x87 and flag emulation. Here the same x87 steps run through the same
 * x86.h helpers in the same order (FLD ST0, FST float, FISTP, FILD, FSUBP or two FSTPs), so the result in EDX:EAX,
 * ECX, the x87 stack, its tags and the status word match the translation; the scratch words on the stack below the
 * caller's ESP, which nobody reads afterwards, are not written. The result: ST0 truncated toward zero as int64
 * (the "integer indefinite" 0x8000000000000000 with IE for NaN, infinity or out of range, as FISTP gives), ST0 popped.
 *
 * RECOMP_NATIVE=0 (all natives) or RECOMP_OPT_NATIVEFTOL=0 (this one) run the translation; the A/B switch
 * nativeftol (bit 35) runs it in OLD periods. RECOMP_NATIVE_SELFTEST=1 compares both on random and edge values the
 * first time the helper is called and reports any difference; =2 (TEST control) skews one native result. */
#include "runtime/guest.h"
extern "C" {
#include "funcs.h"
}
#include "runtime/memory.h"
#include "platform/ab_phase.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>

extern "C" void native_007c4b80(X86 *c);

namespace {

int g_native = -1;   // 1 native, 0 translated; -1 until the environment has been read
void selftest(X86 *c, bool skew);

int native_on(X86 *c) {
    if (__builtin_expect(g_native >= 0, 1))
        return g_native;
    const char *e = getenv("RECOMP_NATIVE"), *k = getenv("RECOMP_OPT_NATIVEFTOL");
    const int want = !(e && e[0] == '0') && !(k && k[0] == '0');
    g_native = 0;   // the self-test's translated half must not recurse into the native one
    const char *t = getenv("RECOMP_NATIVE_SELFTEST");
    // Run 31: =2 is the test phase's fail-on-purpose control (one native result skewed: must print "1 differ").
    if (t && (t[0] == '1' || t[0] == '2'))
        selftest(c, t[0] == '2');
    g_native = want;
    return g_native;
}

uint32_t fbits(float f) {
    uint32_t u;
    memcpy(&u, &f, sizeof u);
    return u;
}

void ftol(X86 *c) {
    const uint32_t esp = c->r[R_ESP];
    fpush_st(c, 0);                                     // FLD ST0
    const uint32_t xbits = fbits(fto_float(c, ST(c, 0)));   // FST float ptr [ESP+0x18] (read back as EDX)
    const int64_t q = fist_i64(c);                      // FISTP qword ptr [ESP+0x10]
    fdrop(c);
    fpush_int(c, q);                                    // FILD qword ptr [ESP+0x10]
    uint32_t eax = (uint32_t)q;
    const uint32_t hi = (uint32_t)((uint64_t)q >> 32);
    uint32_t edx;
    if (eax == 0 && (hi & 0x7fffffffu) == 0) {   // 0 or the indefinite: FSTP, FSTP (the stores are scratch)
        fdrop(c);
        fdrop(c);
        edx = hi;
    } else {
        const uint32_t sign = eax != 0 ? xbits : hi;   // TEST EDX,EDX sees the float bits, or the high word via 007c4bdf
        fset(c, 1, fx87(c, ST(c, 1) - ST(c, 0)));     // FSUBP: x - round(x)
        fdrop(c);
        uint32_t ecx = fbits(fto_float(c, ST(c, 0)));   // FSTP float ptr [ESP]; MOV ECX,[ESP]
        fdrop(c);
        if ((int32_t)sign < 0) {   // XOR ECX,0x80000000; ADD ECX,0x7fffffff; ADC EAX,0; ADC EDX,0
            ecx ^= 0x80000000u;
            const uint64_t t = (uint64_t)ecx + 0x7fffffffu;
            ecx = (uint32_t)t;
            const uint64_t a = (uint64_t)eax + (uint32_t)(t >> 32);
            eax = (uint32_t)a;
            edx = hi + (uint32_t)(a >> 32);
        } else {   // ADD ECX,0x7fffffff; SBB EAX,0; SBB EDX,0
            const uint64_t t = (uint64_t)ecx + 0x7fffffffu;
            ecx = (uint32_t)t;
            const uint64_t a = (uint64_t)eax - (uint32_t)(t >> 32);
            eax = (uint32_t)a;
            edx = hi - (uint32_t)((a >> 32) & 1u);
        }
        c->r[R_ECX] = ecx;
    }
    c->r[R_EAX] = eax;
    c->r[R_EDX] = edx;
    c->eip = rd32(esp);   // RET (EBP was pushed and popped: unchanged)
    c->r[R_ESP] = esp + 4;
}

double rnd_value(int i) {
    static const double edge[] = {0.0, -0.0, 0.5, -0.5, 1.5, -1.5, 2.5, -2.5, 0.49999999, -0.49999999, 1.0, -1.0,
                                  4294967295.0, 4294967296.0, -4294967296.0, 4294967296.5, -4294967296.5,
                                  2147483647.5, -2147483648.5, 9007199254740993.0, 9.2233720368547758e18,
                                  -9.2233720368547758e18, 1e19, -1e19, INFINITY, -INFINITY, NAN, 1e-300, -1e-300};
    const int n = (int)(sizeof edge / sizeof edge[0]);
    if (i < n)
        return edge[i];
    const double m = (double)rand() / RAND_MAX - 0.5;
    const int ex = rand() % 70 - 6;
    return std::ldexp(m, ex) + ((rand() & 3) == 0 ? (double)(rand() % 1000) : 0.0);
}

void selftest(X86 *c, bool skew) {
    const uint32_t stack = c->r[R_ESP] - 0x20000;
    uint32_t ret = 0x00401001u;   // not a function entry (the translated RET tail-calls an entry: culling.c note)
    while (recomp_index_of(ret) >= 0 || recomp_module_lookup(ret) >= 0)
        ++ret;
    int bad = 0, runs = 0;
    for (int i = 0; i < 4000; ++i) {
        const double v = rnd_value(i);
        for (unsigned rc = 0; rc < 4; ++rc) {
            X86 a = *c;
            a.fpu_cw = (uint16_t)((a.fpu_cw & ~0x0c00u) | (rc << 10));
            a.fpu_sw = 0;
            fpush(&a, v);
            a.r[R_ESP] = stack;
            a.r[R_ECX] = 0x13579bdfu;
            wr32(stack, ret);
            X86 b = a;
            fn_007c4b80(&a);
            ftol(&b);
            if (skew && i == 0 && rc == 0)
                b.r[R_EAX] ^= 1u;   // the control's skew (RECOMP_NATIVE_SELFTEST=2 only)
            bool same = a.r[R_EAX] == b.r[R_EAX] && a.r[R_EDX] == b.r[R_EDX] && a.r[R_ECX] == b.r[R_ECX] &&
                        a.r[R_ESP] == b.r[R_ESP] && a.r[R_EBP] == b.r[R_EBP] && a.eip == b.eip &&
                        a.fpu_top == b.fpu_top && a.fpu_tag == b.fpu_tag && a.fpu_sw == b.fpu_sw;
            for (unsigned k = 0; k < 8 && same; ++k)
                if (!fempty(&a, k))
                    same = memcmp(&ST(&a, k), &ST(&b, k), sizeof(double)) == 0;
            ++runs;
            if (!same && ++bad <= 8)
                fprintf(stderr, "native self-test 007c4b80: %.17g rc %u: translated %08x:%08x ecx %08x top %u sw %04x, "
                                "native %08x:%08x ecx %08x top %u sw %04x\n",
                        v, rc, a.r[R_EDX], a.r[R_EAX], a.r[R_ECX], a.fpu_top, a.fpu_sw, b.r[R_EDX], b.r[R_EAX],
                        b.r[R_ECX], b.fpu_top, b.fpu_sw);
        }
    }
    fprintf(stderr, "native self-test 007c4b80 (_ftol): %d runs, %d differ\n", runs, bad);
}

} // namespace

extern "C" void native_007c4b80(X86 *c) {
    if (!native_on(c) || recomp_ab::old(recomp_ab::NATIVEFTOL)) {
        fn_007c4b80(c);
        return;
    }
    ftol(c);
}
