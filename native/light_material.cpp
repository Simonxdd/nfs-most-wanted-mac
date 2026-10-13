/* light_material.cpp - 006c76e0, the light material's shader constants (eLightMaterialData -> diffuse / specular /
 * envmap min-max vectors and powers, uploaded through the current effect or, for the car on the fixed path, packed into
 * a device material), native (R3 run 32; review/run31/native-design.md A; profile 0.25 / 0.27 / 0.19 ms per frame
 * cruise / burnout / front end, the largest self time the design ranked).
 *
 * cdecl (material, flags), RET. Every guest read and write of the original in the original's order, its 0x114-byte
 * aligned frame included (the callees read the vectors there by pointer), every guest call kept as a guest call with
 * the original's arguments, return address and ESP: the effect's SetVector (+0x88) and SetFloat (+0x78) through
 * [0x982c80]+0x48, the device's +0xc4 and +0xe4 through [0x982bdc], and _ftol (007c4b80) four times. The x87 stack
 * lives in a local copy for the arithmetic (the same push / set / drop / exchange / compare / precision rules as the
 * kit's helpers, so the same values, tags, stale slots and status word), written back to the guest state before each
 * call and at the RET and read again after each call; the flags are the ones the translation computes, in the same
 * places. What goes is the per-instruction register-file and x87 bookkeeping through memory.
 *
 * OPT-IN since run 32: RECOMP_OPT_NATIVE_LIGHTMAT=1 turns it on (RECOMP_NATIVE=0 still turns every native off). It is
 * exact (R32V: the shadow compared 256 real calls, 95 of them uploads, 0 differ; on-vs-killed pictures identical) but
 * the R32V cruise profile measured no gain (its body 0.25 ms per frame against the translation's 0.23): the function's
 * cost is its ~100 guest memory accesses and the x87 rounding rules, which the translation already compiles about as
 * well. The fixed path (type 4, [ECX+0x40] == 0) was never reached by the shadow.
 * RECOMP_NATIVE_SELFTEST=1: random input cannot be used (the calls reach the live effect and device), so 256 real
 * calls (the first 64, then every 16th) run the translation, then the native again from the same state and frame bytes (the uploads are
 * idempotent: the same values twice), and compare registers, flags, the x87 state and the frame; a difference runs the
 * translation once more from the start, so its state and uploads are the ones left. The summary counts the paths the
 * shadowed calls took. A guest fault inside the native sees the entry registers (they are written back at the calls
 * and the RET), as in the other natives. */
#include "runtime/guest.h"
extern "C" {
#include "funcs.h"
}
#include "runtime/memory.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>

extern "C" void native_006c76e0(X86 *c);

namespace {

int g_native = -1, g_shadow = 0, g_shadow_runs = 0, g_shadow_bad = 0;
// the shadow's coverage: the path each native run took (bits), counted over the shadowed calls
unsigned g_path = 0, g_path_hits[7], g_calls = 0;
enum { P_ZERO = 1, P_FIXED = 2, P_CLAMP_LO = 4, P_CLAMP_HI = 8, P_EFFECT = 16, P_EARLY = 32, P_OTHER = 64 };

int native_on() {
    if (__builtin_expect(g_native >= 0, 1))
        return g_native;
    // Opt-in (run 32): exact (R32V: 95 shadowed upload calls, identical pictures) but no measured gain (R32V cruise
    // profile: 0.25 ms per frame, the translation 0.23), so the translation stays the default.
    const char *e = getenv("RECOMP_NATIVE"), *k = getenv("RECOMP_OPT_NATIVE_LIGHTMAT");
    g_native = !(e && e[0] == '0') && k && k[0] == '1';
    const char *t = getenv("RECOMP_NATIVE_SELFTEST");
    g_shadow = t && t[0] == '1';
    return g_native;
}

// The x87 stack, as the kit's helpers keep it (x86.h: fpush, fpush_st, fset, fdrop, fxch, fx87, fcom, fstsw).
struct Fpu {
    double st[8];
    uint64_t bits[8];
    uint8_t exact[8];
    uint32_t top;
    uint16_t tag, sw, cw;

    void load(const X86 *c) {
        memcpy(st, c->st, sizeof st);
        memcpy(bits, c->st_bits, sizeof bits);
        memcpy(exact, c->st_exact, sizeof exact);
        top = c->fpu_top;
        tag = c->fpu_tag;
        sw = c->fpu_sw;
        cw = c->fpu_cw;
    }
    void store(X86 *c) const {
        memcpy(c->st, st, sizeof st);
        memcpy(c->st_bits, bits, sizeof bits);
        memcpy(c->st_exact, exact, sizeof exact);
        c->fpu_top = top;
        c->fpu_tag = tag;
        c->fpu_sw = sw;
    }
    unsigned tag_of(unsigned phys) const { return (tag >> (2u * (phys & 7u))) & 3u; }
    void put_tag(unsigned phys, unsigned t) {
        const unsigned sh = 2u * (phys & 7u);
        tag = (uint16_t)((tag & ~(3u << sh)) | (t << sh));
    }
    double S(unsigned i) const { return st[(top + i) & 7u]; }
    void push(double v) {
        top = (top - 1u) & 7u;
        st[top] = v;
        bits[top] = 0;
        exact[top] = 0;
        put_tag(top, ftag_classify(v));
    }
    void push_st(unsigned i) {
        const unsigned src = (top + i) & 7u;
        const uint64_t b = bits[src];
        const uint8_t e = exact[src];
        const unsigned t = tag_of(src);
        push(st[src]);
        bits[top] = b;
        exact[top] = e;
        put_tag(top, t);
    }
    void set(unsigned i, double v) {
        const unsigned phys = (top + i) & 7u;
        st[phys] = v;
        bits[phys] = 0;
        exact[phys] = 0;
        put_tag(phys, ftag_classify(v));
    }
    void drop() {
        exact[top] = 0;
        put_tag(top, FTAG_EMPTY);
        top = (top + 1u) & 7u;
    }
    void xch1() {
        const unsigned a = top & 7u, b = (top + 1u) & 7u;
        const double v = st[a];
        const unsigned t = tag_of(a);
        const uint64_t bb = bits[a];
        const uint8_t e = exact[a];
        st[a] = st[b];
        bits[a] = bits[b];
        exact[a] = exact[b];
        put_tag(a, tag_of(b));
        st[b] = v;
        bits[b] = bb;
        exact[b] = e;
        put_tag(b, t);
    }
    double x87(double r) {
        if (r != r) {
            sw |= 0x0001u;
            return x87_indefinite();
        }
        if (((cw >> 8) & 3u) == 0u)
            return (double)(float)r;
        return r;
    }
    void com(double a, double b) {   // FCOM (any NaN raises IE)
        uint16_t s = (uint16_t)(sw & (uint16_t)~0x4700u);
        if (isnan(a) || isnan(b))
            s |= 0x4501u;
        else if (a < b)
            s |= 0x0100u;
        else if (a == b)
            s |= 0x4000u;
        sw = s;
    }
    uint16_t fstsw() const { return (uint16_t)((sw & (uint16_t)~0x3800u) | (uint16_t)(top << 11)); }
    // the common forms: ST0 op= m32, and FSTP m32 / FST m32
    void mul(double m) { set(0, x87(S(0) * m)); }
    void add(double m) { set(0, x87(S(0) + m)); }
    void sub(double m) { set(0, x87(S(0) - m)); }
};

inline void test_flags(X86 *c, uint32_t r, unsigned bits) {   // TEST / OR: CF OF 0, ZF SF PF (AF left as it is)
    c->eflags_cf = 0;
    c->eflags_of = 0;
    c->eflags_zf = r == 0;
    c->eflags_sf = (r >> (bits - 1u)) & 1u;
    c->eflags_pf = parity8(r);
}

inline void cmp_flags(X86 *c, uint32_t a, uint32_t b) {   // CMP (32 bit)
    const uint64_t rf = (uint64_t)a - b;
    const uint32_t r = (uint32_t)rf;
    c->eflags_cf = (uint32_t)((rf >> 32) & 1u);
    c->eflags_of = ((a ^ b) & (a ^ r)) >> 31;
    c->eflags_af = ((a ^ b ^ r) >> 4) & 1u;
    c->eflags_zf = r == 0;
    c->eflags_sf = r >> 31;
    c->eflags_pf = parity8(r);
}

struct Regs {
    uint32_t eax, ecx, edx, ebx, esp, ebp, esi, edi;
    void put(X86 *c) const {
        c->r[R_EAX] = eax; c->r[R_ECX] = ecx; c->r[R_EDX] = edx; c->r[R_EBX] = ebx;
        c->r[R_ESP] = esp; c->r[R_EBP] = ebp; c->r[R_ESI] = esi; c->r[R_EDI] = edi;
    }
    void get(const X86 *c) {
        eax = c->r[R_EAX]; ecx = c->r[R_ECX]; edx = c->r[R_EDX]; ebx = c->r[R_EBX];
        esp = c->r[R_ESP]; ebp = c->r[R_EBP]; esi = c->r[R_ESI]; edi = c->r[R_EDI];
    }
    void push(uint32_t v) {
        esp -= 4u;
        wr32(esp, v);
    }
};

// A guest call with the x87 stack and the registers handed over and taken back.
#define GUEST_CALL(ret, call)                                                                                          \
    do {                                                                                                               \
        g.push(ret);                                                                                                   \
        g.put(c);                                                                                                      \
        f.store(c);                                                                                                    \
        call;                                                                                                          \
        g.get(c);                                                                                                      \
        f.load(c);                                                                                                     \
    } while (0)

// The effect's SetVector(handle at [[0x982c80]+0x44]+off, &frame[ESP+vec]) as 006c7bcf.. does it.
#define SET_VECTOR(off, vec, ret)                                                                                      \
    do {                                                                                                               \
        g.eax = rd32(g.ecx + 0x48u);                                                                                   \
        g.ecx = rd32(g.ecx + 0x44u);                                                                                   \
        g.ecx = rd32(g.ecx + (off));                                                                                   \
        g.edx = rd32(g.eax);                                                                                           \
        g.edi = g.esp + (vec);                                                                                         \
        g.push(g.edi);                                                                                                 \
        g.push(g.ecx);                                                                                                 \
        g.push(g.eax);                                                                                                 \
        const uint32_t t_ = rd32(g.edx + 0x88u);                                                                       \
        GUEST_CALL(ret, recomp_call(c, t_));                                                                           \
    } while (0)

bool material(X86 *c) {
    if (!gm_valid(c->r[R_ESP] - 0x180u, 0x18cu))
        return false;
    Regs g;
    g.get(c);
    Fpu f;
    f.load(c);
    g_path = 0;
    g.push(g.ebp);                                   // 006c76e0
    g.ebp = g.esp;
    g.esp = (g.esp & 0xfffffff0u) - 0x114u;
    g.push(g.ebx);
    g.push(g.esi);
    g.esi = rd32(g.ebp + 0x8u);                      // the material
    f.push(rdf32(g.esi + 0x34u));
    g.eax = rd32(g.esi + 0x50u);
    f.mul(rdf32(g.esi + 0x30u));
    g.ecx = rd32(0x9b392cu);
    wr32(g.esp + 0x18u, g.eax);
    g.push(g.edi);
    wrf32(g.esp + 0x10u, fto_float(c, f.S(0)));    // diffuse min * scale
    f.drop();
    f.push(rdf32(g.esi + 0x38u));
    f.mul(rdf32(g.esi + 0x30u));
    wrf32(g.esp + 0x14u, fto_float(c, f.S(0)));
    f.drop();
    f.push(rdf32(g.esi + 0x3cu));
    f.mul(rdf32(g.esi + 0x30u));
    wrf32(g.esp + 0x18u, fto_float(c, f.S(0)));
    f.drop();
    f.push(rdf32(g.esi + 0x44u));                   // diffuse max * scale - min
    f.mul(rdf32(g.esi + 0x40u));
    f.sub(rdf32(g.esp + 0x10u));
    wrf32(g.esp + 0x40u, fto_float(c, f.S(0)));
    f.drop();
    f.push(rdf32(g.esi + 0x48u));
    f.mul(rdf32(g.esi + 0x40u));
    f.sub(rdf32(g.esp + 0x14u));
    wrf32(g.esp + 0x44u, fto_float(c, f.S(0)));
    f.drop();
    f.push(rdf32(g.esi + 0x4cu));
    f.mul(rdf32(g.esi + 0x40u));
    f.sub(rdf32(g.esp + 0x18u));
    wrf32(g.esp + 0x48u, fto_float(c, f.S(0)));
    f.drop();
    f.push(rdf32(g.esi + 0x54u));                   // the alphas
    f.sub(rdf32(g.esp + 0x1cu));
    f.mul(rdf32(0x8910f0u));
    wrf32(g.esp + 0x4cu, fto_float(c, f.S(0)));
    f.drop();
    f.push(rdf32(g.ecx + 0x60u));                   // 006c7758: the view's specular scale
    g.ecx = rd32(0x982c80u);
    f.push_st(0);
    wr32(g.esp + 0x3cu, 0);
    f.mul(rdf32(g.esi + 0x5cu));
    wr32(g.esp + 0x5cu, 0);
    f.xch1();
    f.mul(rdf32(g.esi + 0x6cu));
    wrf32(g.esp + 0x2cu, fto_float(c, f.S(0)));
    f.drop();
    f.push_st(0);
    f.mul(rdf32(g.esi + 0x60u));
    wrf32(g.esp + 0x30u, fto_float(c, f.S(0)));
    f.drop();
    f.push_st(0);
    f.mul(rdf32(g.esi + 0x64u));
    wrf32(g.esp + 0x34u, fto_float(c, f.S(0)));
    f.drop();
    f.mul(rdf32(g.esi + 0x68u));
    wrf32(g.esp + 0x38u, fto_float(c, f.S(0)));
    f.drop();
    f.push(rdf32(g.esp + 0x2cu));                   // 006c7798
    f.mul(rdf32(g.esi + 0x70u));
    f.sub(rdf32(g.esp + 0x30u));
    wrf32(g.esp + 0x50u, fto_float(c, f.S(0)));
    f.drop();
    f.push(rdf32(g.esp + 0x2cu));
    f.mul(rdf32(g.esi + 0x74u));
    f.sub(rdf32(g.esp + 0x34u));
    wrf32(g.esp + 0x54u, fto_float(c, f.S(0)));
    f.drop();
    f.push(rdf32(g.esp + 0x2cu));
    f.mul(rdf32(g.esi + 0x78u));
    f.sub(rdf32(g.esp + 0x38u));
    wrf32(g.esp + 0x58u, fto_float(c, f.S(0)));
    f.drop();
    {
        const uint32_t al = rd8(g.ecx + 0x40u);     // 006c77c5
        g.eax = (g.eax & 0xffffff00u) | al;
        test_flags(c, al, 8);
        f.push(rdf32(al ? 0x89096cu : 0x8933d4u));
    }
    f.push(rdf32(g.esi + 0x84u));                   // 006c77da: the envmap
    g.eax = rd32(g.ebp + 0xcu);                      // flags
    const bool keep = ((g.eax >> 8) & 1u) == 0;      // TEST AH,0x1 (its ZF is read at 006c7876)
    test_flags(c, (g.eax >> 8) & 1u, 8);
    f.mul(rdf32(g.esi + 0x80u));
    wr32(g.esp + 0x6cu, 0);
    wr32(g.esp + 0x9cu, 0);
    f.set(0, f.x87(f.S(0) * f.S(1)));
    wrf32(g.esp + 0x60u, fto_float(c, f.S(0)));
    f.drop();
    f.push(rdf32(g.esi + 0x88u));
    f.mul(rdf32(g.esi + 0x80u));
    f.set(0, f.x87(f.S(0) * f.S(1)));
    wrf32(g.esp + 0x64u, fto_float(c, f.S(0)));
    f.drop();
    f.push(rdf32(g.esi + 0x8cu));
    f.mul(rdf32(g.esi + 0x80u));
    f.set(0, f.x87(f.S(0) * f.S(1)));
    wrf32(g.esp + 0x68u, fto_float(c, f.S(0)));
    f.drop();
    f.push(rdf32(g.esi + 0x94u));
    f.mul(rdf32(g.esi + 0x90u));
    f.set(0, f.x87(f.S(0) * f.S(1)));
    f.sub(rdf32(g.esp + 0x60u));
    wrf32(g.esp + 0x90u, fto_float(c, f.S(0)));
    f.drop();
    f.push(rdf32(g.esi + 0x98u));
    f.mul(rdf32(g.esi + 0x90u));
    f.set(0, f.x87(f.S(0) * f.S(1)));
    f.sub(rdf32(g.esp + 0x64u));
    wrf32(g.esp + 0x94u, fto_float(c, f.S(0)));
    f.drop();
    f.push(rdf32(g.esi + 0x9cu));
    f.mul(rdf32(g.esi + 0x90u));
    f.set(0, f.x87(f.S(0) * f.S(1)));
    f.sub(rdf32(g.esp + 0x68u));
    wrf32(g.esp + 0x98u, fto_float(c, f.S(0)));
    f.drop();
    f.drop();                                        // 006c7874 FSTP ST0
    if (!keep) {                                     // 006c787c: flags bit 8 with [0x9017b8]: no diffuse / specular
        g.edx = rd32(0x9017b8u);
        test_flags(c, g.edx, 32);
        if (g.edx != 0) {
            test_flags(c, g.eax & 0xffu, 8);
            if (!(g.eax & 0x80u)) {
                g_path |= P_ZERO;
                static const uint8_t zero[] = {0x10, 0x14, 0x18, 0x1c, 0x40, 0x44, 0x48, 0x4c,
                                               0x30, 0x34, 0x38, 0x3c, 0x50, 0x54, 0x58, 0x5c};
                for (uint8_t o : zero)
                    wr32(g.esp + o, 0);
            }
        }
    }
    g.eax = rd32(g.ecx + 0x4u);                      // 006c7912: the effect type
    cmp_flags(c, g.eax, 4);
    if (g.eax == 4) {
        const uint32_t dl = rd8(g.ecx + 0x40u);
        g.edx = (g.edx & 0xffffff00u) | dl;
        test_flags(c, dl, 8);
        if (dl == 0)
            goto fixed;
        cmp_flags(c, g.eax, 4);                      // 006c7bc1
    } else {
        cmp_flags(c, g.eax, 2);                      // 006c7bc6
        if (g.eax != 2) {
            g_path |= P_OTHER;   // neither effect type: no upload
            goto out;
        }
    }
    // 006c7bcf: the effect's constants
    g_path |= P_EFFECT;
    SET_VECTOR(0x254u, 0x10u, 0x6c7beau);
    g.ecx = rd32(0x982c80u);
    SET_VECTOR(0x27cu, 0x40u, 0x6c7c0bu);
    g.ecx = rd32(0x982c80u);
    {
        const uint32_t t = rd32(g.ecx + 0x4u);
        cmp_flags(c, t, 4);
        if (t != 4) {
            g_path |= P_EARLY;
            goto out;
        }
    }
    SET_VECTOR(0xcccu, 0x30u, 0x6c7c36u);
    g.ecx = rd32(0x982c80u);
    SET_VECTOR(0xd1cu, 0x50u, 0x6c7c57u);
    g.ecx = rd32(0x982c80u);
    SET_VECTOR(0x394u, 0x60u, 0x6c7c78u);
    g.ecx = rd32(0x982c80u);
    SET_VECTOR(0x36cu, 0x90u, 0x6c7c9cu);
    f.push(rdf32(0x9018e0u));                       // 006c7c9c: SetFloat(specular power)
    g.ecx = rd32(0x982c80u);
    f.mul(rdf32(g.esi + 0x58u));
    g.eax = rd32(g.ecx + 0x48u);
    g.edx = rd32(g.eax);
    g.push(g.ecx);
    g.ecx = rd32(g.ecx + 0x44u);
    wrf32(g.esp, fto_float(c, f.S(0)));
    f.drop();
    g.ecx = rd32(g.ecx + 0xcf4u);
    g.push(g.ecx);
    g.push(g.eax);
    {
        const uint32_t t = rd32(g.edx + 0x78u);
        GUEST_CALL(0x6c7cc2u, recomp_call(c, t));
    }
    f.push(rdf32(0x9018dcu));                       // SetFloat(envmap power)
    g.ecx = rd32(0x982c80u);
    f.mul(rdf32(g.esi + 0x7cu));
    g.eax = rd32(g.ecx + 0x48u);
    g.edx = rd32(g.eax);
    g.push(g.ecx);
    g.ecx = rd32(g.ecx + 0x44u);
    wrf32(g.esp, fto_float(c, f.S(0)));
    f.drop();
    g.ecx = rd32(g.ecx + 0x3bcu);
    g.push(g.ecx);
    g.push(g.eax);
    {
        const uint32_t t = rd32(g.edx + 0x78u);
        GUEST_CALL(0x6c7ce8u, recomp_call(c, t));
    }
    goto out;

fixed: {                                             // 006c7929: the car material on the fixed path
    g_path |= P_FIXED;
    f.push(rdf32(g.esp + 0x40u));
    wr32(g.esp + 0x70u, 0x3f800000u);
    f.mul(rdf32(0x8933d4u));
    wr32(g.esp + 0x7cu, 0x3f800000u);
    wr32(g.esp + 0x74u, 0x3f800000u);
    wr32(g.esp + 0x88u, 0);
    f.add(rdf32(g.esp + 0x10u));
    wr32(g.esp + 0x80u, 0);
    f.push(rdf32(g.esp + 0x44u));
    wr32(g.esp + 0x78u, 0x3f800000u);
    f.mul(rdf32(0x8933d4u));
    wr32(g.esp + 0x84u, 0);
    wr32(g.esp + 0x8cu, 0x3f800000u);
    wr32(g.esp + 0xd4u, 0x3f800000u);
    f.add(rdf32(g.esp + 0x14u));
    wr32(g.esp + 0xf4u, 0x3f800000u);
    f.push(rdf32(g.esp + 0x48u));
    f.mul(rdf32(0x8933d4u));
    f.add(rdf32(g.esp + 0x18u));
    wrf32(g.esp + 0x118u, fto_float(c, f.S(0)));
    f.drop();
    g.edx = rd32(g.esp + 0x118u);
    f.push(rdf32(g.esp + 0x50u));
    wr32(g.esp + 0xd0u, g.edx);
    f.mul(rdf32(0x8933d4u));
    f.add(rdf32(g.esp + 0x30u));
    wrf32(g.esp + 0xa0u, fto_float(c, f.S(0)));
    f.drop();
    g.eax = rd32(g.esp + 0xa0u);
    f.push(rdf32(g.esp + 0x54u));
    wr32(g.esp + 0xe8u, g.eax);
    f.mul(rdf32(0x8933d4u));
    g.eax = rd32(g.esp + 0x70u);
    wr32(g.esp + 0xd8u, g.eax);
    g.eax = rd32(g.esp + 0x7cu);
    f.add(rdf32(g.esp + 0x34u));
    wr32(g.esp + 0xe4u, g.eax);
    g.eax = rd32(g.esp + 0x88u);
    wr32(g.esp + 0x100u, g.eax);
    wrf32(g.esp + 0xa4u, fto_float(c, f.S(0)));
    f.drop();
    g.ecx = rd32(g.esp + 0xa4u);
    f.push(rdf32(g.esp + 0x58u));
    wr32(g.esp + 0xecu, g.ecx);
    f.mul(rdf32(0x8933d4u));
    g.ecx = rd32(g.esp + 0x74u);
    wr32(g.esp + 0xdcu, g.ecx);
    g.ecx = rd32(g.esp + 0x80u);
    f.add(rdf32(g.esp + 0x38u));
    wr32(g.esp + 0xf8u, g.ecx);
    g.ecx = rd32(g.esp + 0x8cu);
    wr32(g.esp + 0x104u, g.ecx);
    wrf32(g.esp + 0xa8u, fto_float(c, f.S(0)));
    f.drop();
    g.edx = rd32(g.esp + 0xa8u);
    f.push(rdf32(g.esp + 0x90u));
    wr32(g.esp + 0xf0u, g.edx);
    f.mul(rdf32(0x8910f0u));
    g.edx = rd32(g.esp + 0x78u);
    wr32(g.esp + 0xe0u, g.edx);
    g.edx = rd32(g.esp + 0x84u);
    f.add(rdf32(g.esp + 0x60u));
    wr32(g.esp + 0xfcu, g.edx);
    wrf32(g.esp + 0xb0u, fto_float(c, f.S(0)));
    f.drop();
    f.push(rdf32(g.esp + 0x94u));
    f.mul(rdf32(0x8910f0u));
    f.add(rdf32(g.esp + 0x64u));
    wrf32(g.esp + 0xb4u, fto_float(c, f.S(0)));
    f.drop();
    f.push(rdf32(g.esp + 0x98u));
    f.mul(rdf32(0x8910f0u));
    f.add(rdf32(g.esp + 0x68u));
    wrf32(g.esp + 0xb8u, fto_float(c, f.S(0)));
    f.drop();
    f.xch1();                                        // 006c7adf
    wrf32(g.esp + 0xc8u, fto_float(c, f.S(0)));
    f.drop();
    wrf32(g.esp + 0xccu, fto_float(c, f.S(0)));
    f.drop();
    f.push(rdf32(0x8fae84u));                       // 006c7aef: the specular power, clamped to 0.1 .. 20
    f.mul(rdf32(g.esi + 0x58u));
    wrf32(g.esp + 0x108u, fto_float(c, f.S(0)));
    f.com(f.S(0), rdf32(0x890cd4u));
    f.drop();
    g.eax = (g.eax & 0xffff0000u) | f.fstsw();
    test_flags(c, (g.eax >> 8) & 0x5u, 8);
    if (!c->eflags_pf) {
        g_path |= P_CLAMP_LO;
        wr32(g.esp + 0x108u, 0x3dcccccdu);
    } else {
        f.push(rdf32(g.esp + 0x108u));
        f.com(f.S(0), rdf32(0x894b64u));
        f.drop();
        g.eax = (g.eax & 0xffff0000u) | f.fstsw();
        test_flags(c, (g.eax >> 8) & 0x41u, 8);
        if (c->eflags_zf) {
            g_path |= P_CLAMP_HI;
            wr32(g.esp + 0x108u, 0x41a00000u);
        }
    }
    g.eax = rd32(0x982bdcu);                         // 006c7b38: the device's material
    g.edx = rd32(g.eax);
    g.ecx = g.esp + 0xc8u;
    g.push(g.ecx);
    g.push(g.eax);
    {
        const uint32_t t = rd32(g.edx + 0xc4u);
        GUEST_CALL(0x6c7b4eu, recomp_call(c, t));
    }
    f.push(rdf32(g.esp + 0x4cu));                   // the packed texture factor: four _ftol
    f.add(rdf32(g.esp + 0x1cu));
    g.esi = rd32(0x982bdcu);
    g.edi = rd32(g.esi);
    f.mul(rdf32(0x8a0d64u));
    GUEST_CALL(0x6c7b69u, CALL_FN(007c4b80));
    f.push(rdf32(g.esp + 0xb8u));
    f.mul(rdf32(0x899dd8u));
    g.ebx = shl32_f(c, g.eax, 8);
    GUEST_CALL(0x6c7b80u, CALL_FN(007c4b80));
    f.push(rdf32(g.esp + 0xb4u));
    f.mul(rdf32(0x899dd8u));
    g.ebx = shl32_f(c, g.ebx | g.eax, 8);
    GUEST_CALL(0x6c7b97u, CALL_FN(007c4b80));
    f.push(rdf32(g.esp + 0xb0u));
    f.mul(rdf32(0x899dd8u));
    g.ebx = shl32_f(c, g.ebx | g.eax, 8);
    GUEST_CALL(0x6c7baeu, CALL_FN(007c4b80));
    g.ebx |= g.eax;
    test_flags(c, g.ebx, 32);
    g.push(g.ebx);
    g.push(0x3cu);
    g.push(g.esi);
    {
        const uint32_t t = rd32(g.edi + 0xe4u);
        GUEST_CALL(0x6c7bbau, recomp_call(c, t));
    }
}
out:                                                 // 006c7ce8 / 006c7bba
    g.edi = rd32(g.esp);
    g.esi = rd32(g.esp + 4u);
    g.ebx = rd32(g.esp + 8u);
    g.esp = g.ebp;
    g.ebp = rd32(g.esp);
    c->eip = rd32(g.esp + 4u);                       // RET
    g.esp += 8u;
    g.put(c);
    f.store(c);
    return true;
}

// The shadow check (RECOMP_NATIVE_SELFTEST=1): the first 64 real calls run both from the same state.
void shadow(X86 *c) {
    const uint32_t lo = c->r[R_ESP] - 0x400u;   // the function's frame reaches about 0x150 below; callees go deeper
    static uint8_t before[0x410], after_a[0x410];
    const X86 start = *c;
    memcpy(before, g_mem + lo, sizeof before);
    fn_006c76e0(c);
    const X86 a = *c;
    memcpy(after_a, g_mem + lo, sizeof after_a);
    *c = start;
    memcpy(g_mem + lo, before, sizeof before);
    bool ran = material(c);
    if (!ran)
        fn_006c76e0(c);
    bool same = !memcmp(after_a, g_mem + lo, sizeof after_a) && a.r[R_EAX] == c->r[R_EAX] &&
                a.r[R_ECX] == c->r[R_ECX] && a.r[R_EDX] == c->r[R_EDX] && a.r[R_EBX] == c->r[R_EBX] &&
                a.r[R_ESP] == c->r[R_ESP] && a.r[R_EBP] == c->r[R_EBP] && a.r[R_ESI] == c->r[R_ESI] &&
                a.r[R_EDI] == c->r[R_EDI] && a.eip == c->eip && x86_get_eflags(&a) == x86_get_eflags(c) &&
                a.fpu_top == c->fpu_top && a.fpu_tag == c->fpu_tag && a.fpu_sw == c->fpu_sw;
    for (unsigned k = 0; k < 8 && same; ++k)
        same = !memcmp(&a.st[k], &c->st[k], sizeof(double)) && a.st_bits[k] == c->st_bits[k] &&
               a.st_exact[k] == c->st_exact[k];
    ++g_shadow_runs;
    for (int k = 0; k < 7; ++k)
        g_path_hits[k] += ran && (g_path >> k) & 1u;
    if (!same) {
        if (++g_shadow_bad <= 8)
            fprintf(stderr, "native self-test 006c76e0: call %d differs (eax %08x/%08x ecx %08x/%08x edx %08x/%08x "
                            "eflags %08x/%08x fsw %04x/%04x frame %d)\n", g_shadow_runs, a.r[R_EAX], c->r[R_EAX],
                    a.r[R_ECX], c->r[R_ECX], a.r[R_EDX], c->r[R_EDX], x86_get_eflags(&a), x86_get_eflags(c),
                    a.fpu_sw, c->fpu_sw, memcmp(after_a, g_mem + lo, sizeof after_a) != 0);
        // run the translation once more from the start, so its uploads (not the native's) are the last ones
        *c = start;
        memcpy(g_mem + lo, before, sizeof before);
        fn_006c76e0(c);
    }
    if (g_shadow_runs == 256) {
        fprintf(stderr, "native self-test 006c76e0 (light material): %d runs, %d differ (shadow of real calls; paths: "
                        "zeroed %u, fixed %u (clamp 0.1 %u, clamp 20 %u), effect %u, early out %u, no upload %u)\n",
                g_shadow_runs, g_shadow_bad, g_path_hits[0], g_path_hits[1], g_path_hits[2], g_path_hits[3],
                g_path_hits[4], g_path_hits[5], g_path_hits[6]);
        g_shadow = 0;
    }
}

} // namespace

extern "C" void native_006c76e0(X86 *c) {
    if (!native_on()) {
        fn_006c76e0(c);
        return;
    }
    // the shadow: the first 64 calls, then every 16th (so the world's effect and fixed paths are reached), 256 in all
    if (__builtin_expect(g_shadow, 0) && (g_calls++ < 64 || (g_calls & 15u) == 0)) {
        shadow(c);
        return;
    }
    if (!material(c))
        fn_006c76e0(c);
}
