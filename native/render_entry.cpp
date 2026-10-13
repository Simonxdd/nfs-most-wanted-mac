/* render_entry.cpp - 006e0ab0, append one render entry to the world renderer's list (0x44 bytes at 0x93e878 + n*0x44
 * and the {n, sort key} pair at [0x9877b4] + n*8), native (R3 run 32; review/run31/native-design.md D; profile 0.13 /
 * 0.17 / 0.10 ms per frame cruise / burnout / front end).
 *
 * cdecl, nine arguments, RET. Every guest read and write of the original in the original's order, its frame included
 * (the pushes, the aligned locals the box and centre live in), so the result is the same under any aliasing and the
 * native never has to give up half way. The three guest calls stay guest calls with the original's frame and return
 * addresses: 006c6f30 (the distance key of a solid whose +0x54 is 2), 006be800 (transform the mesh box, native) and
 * 0045ed20 (-> 006bd940, transform the box centre); the LOD choice keeps the x87 through the kit's own helpers (fpush,
 * fx87, fto_float, fcom, fstsw: the same rounding, status word, tags and stack). What goes is the rest: the flags of
 * every integer instruction (only the ones left at the RET are set: CMP ESI,0x1000 on a full list, else ADD ECX,EDX
 * and INC EAX), the register file round trips, the PUSH/POP bookkeeping. The effect-type jump table (0x6e0e04 /
 * 0x6e0dfc, code bytes) is checked once; any target but its two the translation knows turns the native off.
 *
 * RECOMP_NATIVE=0 (all natives) or RECOMP_OPT_NATIVE_RENDERENTRY=0 (this one) run the translation;
 * RECOMP_NATIVE_SELFTEST=1 compares both at the first call on random entries (priority bytes, the distance-key solid,
 * missing textures, a full list, the LOD path with a stand-in view and centre matrix), saving and restoring every
 * global the test writes. */
#include "runtime/guest.h"
extern "C" {
#include "funcs.h"
}
#include "runtime/memory.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>

extern "C" void native_006e0ab0(X86 *c);

namespace {

int g_native = -1;
void selftest(X86 *c);

bool table_ok() {
    for (uint32_t k = 0; k <= 0x12; ++k) {
        const uint32_t t = rd32(rd8(0x6e0e04u + k) * 4u + 0x6e0dfcu);
        if (t != 0x6e0c11u && t != 0x6e0d27u)
            return false;
    }
    return true;
}

int native_on(X86 *c) {
    if (__builtin_expect(g_native >= 0, 1))
        return g_native;
    const char *e = getenv("RECOMP_NATIVE"), *k = getenv("RECOMP_OPT_NATIVE_RENDERENTRY");
    const int want = !(e && e[0] == '0') && !(k && k[0] == '0') && table_ok();
    g_native = 0;
    const char *t = getenv("RECOMP_NATIVE_SELFTEST");
    if (t && t[0] == '1' && table_ok())
        selftest(c);
    g_native = want;
    return g_native;
}

void sub_flags(X86 *c, uint32_t a, uint32_t b) {   // CMP a,b (32 bit)
    const uint64_t rf = (uint64_t)a - b;
    const uint32_t r = (uint32_t)rf;
    c->eflags_cf = (uint32_t)((rf >> 32) & 1u);
    c->eflags_of = ((a ^ b) & (a ^ r)) >> 31;
    c->eflags_af = ((a ^ b ^ r) >> 4) & 1u;
    c->eflags_zf = r == 0;
    c->eflags_sf = r >> 31;
    c->eflags_pf = parity8(r);
}

void sub_flags8(X86 *c, uint32_t a, uint32_t b) {   // CMP byte a,b
    a &= 0xffu;
    b &= 0xffu;
    const uint32_t rf = a - b, r = rf & 0xffu;
    c->eflags_cf = (rf >> 8) & 1u;
    c->eflags_of = (((a ^ b) & (a ^ r)) >> 7) & 1u;
    c->eflags_af = ((a ^ b ^ r) >> 4) & 1u;
    c->eflags_zf = r == 0;
    c->eflags_sf = r >> 7;
    c->eflags_pf = parity8(r);
}

void add_flags(X86 *c, uint32_t a, uint32_t b) {   // ADD a,b (32 bit)
    const uint64_t rf = (uint64_t)a + b;
    const uint32_t r = (uint32_t)rf;
    c->eflags_cf = (uint32_t)((rf >> 32) & 1u);
    c->eflags_of = (~(a ^ b) & (a ^ r)) >> 31;
    c->eflags_af = ((a ^ b ^ r) >> 4) & 1u;
    c->eflags_zf = r == 0;
    c->eflags_sf = r >> 31;
    c->eflags_pf = parity8(r);
}

// TEST AH,mask after FNSTSW AX: the parity the JP / JNP reads, and whether the masked bits are all clear
inline bool ah_parity(uint32_t eax, uint32_t mask) {
    return parity8((eax >> 8) & mask) != 0;
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

bool append(X86 *c) {
    const uint32_t esp0 = c->r[R_ESP];
    if (!gm_valid(esp0 - 0xa0, 0xcc))   // the frame (aligned locals, pushes, call frames) and the nine arguments
        return false;
    Regs g;
    g.get(c);
    g.push(g.ebp);                                   // 006e0ab0
    g.ebp = g.esp;
    g.esp = (g.esp & 0xfffffff0u) - 0x44u;
    g.eax = rd32(g.ebp + 0x18u);
    g.ecx = rd32(g.eax);
    g.ecx = (g.ecx & 0xffffff00u) | rd8(g.ecx + 0x51u);
    g.push(g.ebx);
    g.push(g.esi);
    g.edx &= 0xffffff00u;
    g.push(g.edi);
    if (g.ecx & 0xffu)
        g.edx |= g.ecx & 0xffu;                      // 006e0aca MOV DL,CL
    g.esi = rd32(0x982cdcu);
    if ((int32_t)g.esi >= 0x1000) {                  // 006e0ad2: the list is full
        sub_flags(c, g.esi, 0x1000u);
        goto pops;
    }
    g.ecx = rd32(g.ebp + 0x8u);
    g.esi = g.esi * 0x44u + 0x93e878u;
    g.ebx = rd32(g.ebp + 0x28u);
    g.edi = rd32(g.ebp + 0x14u);
    wr32(g.esi, g.ecx);
    g.ecx = rd32(g.ebp + 0xcu);
    wr32(g.esi + 0x8u, g.ecx);
    g.ecx = rd32(g.ebp + 0x10u);
    wr32(g.esi + 0xcu, g.ecx);
    g.ecx = rd32(g.ebp + 0x20u);
    wr32(g.esi + 0x14u, g.ecx);
    g.ecx = rd32(g.ebp + 0x24u);
    wr32(g.esi + 0x18u, g.ecx);
    g.ecx = rd32(g.ebp + 0x1cu);
    wr32(g.esi + 0x4u, 0);
    wr32(g.esi + 0x1cu, g.ecx);
    wr32(g.esi + 0x10u, g.edi);
    wr32(g.esi + 0x20u, g.ebx);
    wr32(g.esi + 0x3cu, 0);
    wr32(g.esi + 0x40u, 0xbf800000u);
    g.ebx = rd32(g.eax);
    wr32(g.esi + 0x24u, g.ebx);
    g.ebx = rd32(g.eax + 0x4u);
    wr32(g.esi + 0x28u, g.ebx);
    g.ebx = rd32(g.eax + 0x8u);
    wr32(g.esi + 0x2cu, g.ebx);
    g.ebx = rd32(g.eax + 0xcu);
    wr32(g.esi + 0x30u, g.ebx);
    g.ebx = rd32(g.eax + 0x10u);
    wr32(g.esi + 0x34u, g.ebx);
    g.eax = rd32(g.eax);
    {
        const uint32_t kind = rd8(g.eax + 0x54u);
        if (kind == 2) {                             // 006e0b50: the solid's distance key
            sub_flags8(c, kind, 2);
            g.edx = rd32(g.esi + 0x4u);
            g.push(g.ecx);
            g.ecx = rd32(g.esi);
            g.push(g.ecx);
            g.push(g.edx);
            g.push(0x6e0b5du);
            g.put(c);
            CALL_FN(006c6f30);
            g.get(c);
            g.esp += 0xcu;
            g.eax |= 0x80000000u;
            goto key;
        }
    }
    if (g.edx & 0xffu) {                             // 006e0b6e: a priority byte
        g.eax = (uint32_t)(int32_t)(int8_t)(g.edx & 0xffu) | 0x80000000u;
        goto key;
    }
    g.eax = 0;                                       // 006e0b7b: the textures' hash chain
    wr32(g.esi + 0x38u, 0);
    g.edx = rd32(g.esi + 0x24u);
    if (g.edx != 0) {
        g.edx = rd32(g.edx + 0x24u);
        wr32(g.esi + 0x38u, g.edx);
    }
    if (rd32(g.esi + 0x28u) != 0) {
        g.edx = rd32(g.esi + 0x38u);
        g.ebx = rd32(g.esi + 0x28u);
        g.edx = (g.edx << 1) ^ rd32(g.ebx + 0x24u);
        wr32(g.esi + 0x38u, g.edx);
    }
    if (rd32(g.esi + 0x2cu) != 0) {
        g.edx = rd32(g.esi + 0x38u);
        g.ebx = rd32(g.esi + 0x2cu);
        g.edx = (g.edx << 1) ^ rd32(g.ebx + 0x24u);
        wr32(g.esi + 0x38u, g.edx);
    }
    g.edx = rd32(g.esi + 0x30u);
    if (g.edx != 0) {
        g.ebx = rd32(g.esi + 0x38u);
        g.ebx = (g.ebx << 1) ^ rd32(g.edx + 0x24u);
        wr32(g.esi + 0x38u, g.ebx);
    }
    if (rd32(g.esi + 0x34u) != 0) {
        g.edx = rd32(g.esi + 0x38u);
        g.ebx = rd32(g.esi + 0x34u);
        g.edx = (g.edx << 1) ^ rd32(g.ebx + 0x24u);
        wr32(g.esi + 0x38u, g.edx);
    }
    g.ebx = rd32(g.esi + 0x38u);
    g.edx = rd32(g.esi + 0xcu);
    g.ebx ^= g.edx;
    g.edx = rd32(0x982a20u);
    wr32(g.esi + 0x38u, g.ebx);
    wr8(g.esi + 0x3bu, 0);
    {
        const bool player = rd32(g.edx + 0x4u) == 1u;   // 006e0bed
        g.ebx = rd32(g.edi + 0x4u);
        if (!player || g.ebx > 0x12u)
            goto type;
        g.edx = rd8(g.ebx + 0x6e0e04u);
        if (rd32(g.edx * 4u + 0x6e0dfcu) != 0x6e0c11u)
            goto type;
    }
    {                                                // 006e0c11: a LOD'd effect in the player's view
        const uint32_t lods = rd32(0x901830u);
        if ((int32_t)lods <= 0)
            goto lod_none;
        sub_flags(c, lods, g.eax);
        g.eax = rd32(g.ebp + 0x8u);                  // the mesh box into the aligned locals
        g.edx = rd32(g.eax);
        wr32(g.esp + 0x20u, g.edx);
        g.edx = rd32(g.eax + 0x4u);
        wr32(g.esp + 0x24u, g.edx);
        g.edx = rd32(g.eax + 0x8u);
        wr32(g.esp + 0x28u, g.edx);
        g.edx = rd32(g.eax + 0xcu);
        wr32(g.esp + 0x10u, g.edx);
        g.edx = rd32(g.eax + 0x10u);
        g.eax = rd32(g.eax + 0x14u);
        wr32(g.esp + 0x14u, g.edx);
        g.edx = g.esp + 0x10u;
        wr32(g.esp + 0x18u, g.eax);
        g.push(g.edx);
        g.eax = g.esp + 0x24u;
        g.push(g.eax);
        g.push(g.ecx);
        g.push(0x6e0c59u);
        g.put(c);
        CALL_FN(006be800);
        g.get(c);
        fpush(c, (double)rdf32(g.esp + 0x1cu));     // 006e0c59: the centre, (min + max) * 0.5 per axis
        fset(c, 0, fx87(c, ST(c, 0) + (double)rdf32(g.esp + 0x2cu)));
        g.edx = rd32(0x919650u);
        g.ecx = g.esp + 0x4cu;
        g.push(g.ecx);
        fset(c, 0, fx87(c, ST(c, 0) * (double)rdf32(0x8933d4u)));
        add_flags(c, g.edx, 0x80u);
        g.edx += 0x80u;
        g.push(g.edx);
        wrf32(g.esp + 0x54u, fto_float(c, ST(c, 0)));
        fdrop(c);
        g.eax = g.esp + 0x44u;
        fpush(c, (double)rdf32(g.esp + 0x28u));
        g.push(g.eax);
        fset(c, 0, fx87(c, ST(c, 0) + (double)rdf32(g.esp + 0x3cu)));
        fset(c, 0, fx87(c, ST(c, 0) * (double)rdf32(0x8933d4u)));
        wrf32(g.esp + 0x5cu, fto_float(c, ST(c, 0)));
        fdrop(c);
        fpush(c, (double)rdf32(g.esp + 0x30u));
        fset(c, 0, fx87(c, ST(c, 0) + (double)rdf32(g.esp + 0x40u)));
        fset(c, 0, fx87(c, ST(c, 0) * (double)rdf32(0x8933d4u)));
        wrf32(g.esp + 0x60u, fto_float(c, ST(c, 0)));
        fdrop(c);
        g.push(0x6e0cabu);
        g.put(c);
        CALL_FN(0045ed20);
        g.get(c);
        fpush(c, (double)rdf32(0x8fae68u));         // 006e0cab: the distance against the LOD thresholds
        g.esp += 0x18u;
        fcom(c, ST(c, 0), (double)rdf32(0x8fae50u));
        fdrop(c);
        g.eax = (g.eax & 0xffff0000u) | fstsw(c);
        if (!ah_parity(g.eax, 0x41u))
            goto lod_none;
        fpush(c, (double)rdf32(g.esp + 0x38u));
        fcom(c, ST(c, 0), (double)rdf32(0x8fae60u));
        fdrop(c);
        g.eax = (g.eax & 0xffff0000u) | fstsw(c);
        if (((g.eax >> 8) & 0x41u) == 0)
            goto lod_none;
        fpush(c, (double)rdf32(g.esp + 0x38u));
        fcom(c, ST(c, 0), (double)rdf32(0x8fae58u));
        fdrop(c);
        fpush(c, (double)rdf32(g.esp + 0x38u));
        g.eax = (g.eax & 0xffff0000u) | fstsw(c);
        if (((g.eax >> 8) & 0x41u) != 0) {          // 006e0d9b
            fcom(c, ST(c, 0), (double)rdf32(0x8fae58u));
            fdrop(c);
            g.eax = (g.eax & 0xffff0000u) | fstsw(c);
            if (ah_parity(g.eax, 0x41u))
                goto lod_read;
            g.edi = rd32(g.edi + 0x38u);
            g.eax = rd32(0x982c3cu);
            if ((int32_t)g.eax < (int32_t)g.edi)
                g.edi = g.eax;
            wr32(g.esi + 0x3cu, g.edi);
            goto lod_read;
        }
        fcom(c, ST(c, 0), (double)rdf32(0x8fae5cu));
        fdrop(c);
        g.edi = rd32(g.edi + 0x38u);
        g.eax = (g.eax & 0xffff0000u) | fstsw(c);
        const bool far = ah_parity(g.eax, 0x5u);
        g.eax = rd32(0x982c3cu);
        if (far) {                                   // 006e0d70
            g.eax += 0xfffffffeu;
            if ((int32_t)g.edi < (int32_t)g.eax)
                g.eax = g.edi;
            wr32(g.esi + 0x3cu, g.eax);
            if (!((int32_t)g.eax > 1))
                g.eax = 1;
            g.ecx = rd32(g.esi + 0x10u);
            wr32(g.esi + 0x3cu, g.eax);
            if (rd32(g.ecx + 0x4u) != 4u) {
                g.edx = rd32(g.esp + 0x38u);
                wr32(g.esi + 0x40u, g.edx);
            }
            goto lod_read;
        }
        g.eax -= 1u;                                 // 006e0d08
        if ((int32_t)g.edi < (int32_t)g.eax)
            g.eax = g.edi;
        wr32(g.esi + 0x3cu, g.eax);
        if (!((int32_t)g.eax > 1))
            g.eax = 1;
        wr32(g.esi + 0x3cu, g.eax);
        goto lod_read;
    }
lod_none:
    wr32(g.esi + 0x3cu, 0);                         // 006e0d1c (EAX is 0 there) / 006e0dc2
lod_read:
    g.eax = rd32(g.esi + 0x3cu) * 0x1fu;            // 006e0d1f
    g.ebx += g.eax;
type:
    g.eax = rd32(g.esi + 0x38u) | (g.ebx << 24);   // 006e0d27
key: {                                               // 006e0d2f: the entry's key and the {n, key} pair
    g.ecx = rd32(0x9877b4u);
    wr32(g.esi + 0x38u, g.eax);
    g.esi = g.eax;
    g.eax = rd32(0x982cdcu);
    wr32(g.ecx + g.eax * 8u, g.eax);
    g.eax = rd32(0x9877b4u);
    g.edx = rd32(0x982cdcu);
    wr32(g.eax + g.edx * 8u + 0x4u, g.esi);
    if (rd32(0x8fae70u) == 0xffffffffu) {
        wr32(0x8fae70u, 0);
        wr32(0x982cd4u, 1);
    } else {
        wr32(0x982cd4u, rd32(0x982cd4u) + 1u);
    }
    g.ecx = rd32(g.ebp + 0x8u);                      // 006e0dd4: the poly and entry counters
    g.edx = rd32(g.ecx + 0x5cu);
    g.ecx = rd32(0x982cd8u);
    g.eax = rd32(0x982cdcu);
    {
        const uint64_t rf = (uint64_t)g.ecx + g.edx;
        c->eflags_cf = (uint32_t)((rf >> 32) & 1u);
        g.ecx = (uint32_t)rf;
        const uint32_t a = g.eax, r = a + 1u;   // INC EAX: OF AF ZF SF PF
        c->eflags_of = (~(a ^ 1u) & (a ^ r)) >> 31;
        c->eflags_af = ((a ^ 1u ^ r) >> 4) & 1u;
        c->eflags_zf = r == 0;
        c->eflags_sf = r >> 31;
        c->eflags_pf = parity8(r);
        g.eax = r;
    }
    wr32(0x982cd8u, g.ecx);
    wr32(0x982cdcu, g.eax);
}
pops:
    g.edi = rd32(g.esp);
    g.esi = rd32(g.esp + 4u);
    g.ebx = rd32(g.esp + 8u);
    g.esp = g.ebp;
    g.ebp = rd32(g.esp);
    c->eip = rd32(g.esp + 4u);                       // RET
    g.esp += 8u;
    g.put(c);
    return true;
}

// ---------------------------------------------------------------------------------------------------------- self-test

void selftest(X86 *c) {
    const uint32_t stack = (c->r[R_ESP] - 0x20000u) | 0xcu;   // misaligned on purpose (the AND ESP,-16 path)
    const uint32_t area = stack - 0x8000u;                     // mesh, texture array, textures, effect, view, matrix
    uint32_t ret = 0x00401001u;
    while (recomp_index_of(ret) >= 0 || recomp_module_lookup(ret) >= 0)
        ++ret;
    // the counters first (compared), then the view, LOD and threshold words the test stands in for (restored at the end)
    const uint32_t globals[] = {0x982cdcu, 0x982cd8u, 0x982cd4u, 0x8fae70u, 0x982a20u, 0x901830u, 0x919650u, 0x919690u,
                                0x8fae50u, 0x8fae58u, 0x8fae5cu, 0x8fae60u, 0x8fae68u, 0x982c3cu};
    const int NG = (int)(sizeof globals / sizeof globals[0]);
    uint32_t saved_g[NG];
    for (int i = 0; i < NG; ++i)
        saved_g[i] = rd32(globals[i]);
    const uint32_t pairs = rd32(0x9877b4u);
    static uint8_t saved_entry[0x44], saved_pair[8], init[0x2000], out_area[0x2000], out_stack[0x420], out_entry[0x44];
    static uint8_t saved_area[0x2000], saved_stack[0x420];
    const uint32_t STK = stack - 0x3f0u;   // the frames below the scratch stack and its nine arguments
    memcpy(saved_area, g_mem + area, sizeof saved_area);
    memcpy(saved_stack, g_mem + STK, sizeof saved_stack);
    const float thr = rdf32(0x8fae58u) > 0.0f && rdf32(0x8fae58u) < 1e6f ? rdf32(0x8fae58u) : 100.0f;
    int bad = 0, runs = 0, fell_back = 0;
    for (int n = 0; n < 2000; ++n) {
        const uint32_t slot = n % 97 == 13 ? 0x1000u : (uint32_t)rand() % 0x1000u;
        const bool lod = n % 3 != 0;
        // the scratch objects: mesh at +0, texture array at +0x100, five textures at +0x200 (0x60 each),
        // the solid/effect at +0x600, the view at +0x700, the centre matrix at +0x800 (+0x80 used)
        for (unsigned i = 0; i < sizeof init; ++i)
            init[i] = (uint8_t)rand();
        auto w32 = [&](uint32_t a, uint32_t v) { memcpy(init + (a - area), &v, 4); };
        auto wf = [&](uint32_t a, float v) { memcpy(init + (a - area), &v, 4); };
        const uint32_t mesh = area, tex = area + 0x100u, tx = area + 0x200u, eff = area + 0x600u, view = area + 0x700u,
                       mat = area + 0x800u;
        for (int k = 0; k < 6; ++k)   // the mesh box: min then max, around the thresholds
            wf(mesh + 4u * k, (float)((rand() % 2001) - 1000) / 1000.0f * thr * 3.0f);
        for (int k = 0; k < 5; ++k)
            w32(tex + 4u * k, rand() % 5 == 0 ? 0u : tx + 0x60u * k);
        init[tx + 0x51u - area] = rand() % 5 == 0 ? (uint8_t)rand() : 0;   // a priority byte on the first texture
        const bool kind2 = rand() % 7 == 0;
        init[tx + 0x54u - area] = kind2 ? 2 : (uint8_t)(rand() % 4 == 0 ? 3 : 0);
        w32(eff + 0x4u, rand() % 4 == 0 ? (uint32_t)rand() : (uint32_t)rand() % 0x13u);
        w32(eff + 0x38u, (uint32_t)rand() % 8u);
        // view slot 1 whenever 006c6f30 runs (it reads [0x9195e0 + slot*0x70] and +0x40: stood in for slot 1 only)
        w32(view + 0x4u, lod || kind2 ? 1u : (uint32_t)rand() % 3u);
        wf(area + 0xa00u + 0xc0u, (float)(rand() % 1000) / 10.0f);   // [0x919690]+0xc0
        // the LOD thresholds and the LOD cap, random per run (an unordered compare every 23rd run)
        float th[5];
        for (int k = 0; k < 5; ++k)
            th[k] = (float)(rand() % 2001) / 1000.0f * thr * 3.0f;
        if (n % 23 == 11) {
            const uint32_t qnan = 0x7fc00000u;
            memcpy(&th[rand() % 5], &qnan, 4);
        }
        const uint32_t cap = (uint32_t)rand() % 8u;
        for (int k = 0; k < 16; ++k)
            wf(mat + 0x80u + 4u * k, (k % 5 == 0) ? 1.0f : (k >= 12 ? (float)(rand() % 200 - 100) : 0.0f));
        w32(tex, tx);   // the first texture is read through unconditionally (the original reads [[EAX]+0x51])
        const uint32_t args[9] = {mesh, (uint32_t)rand(), (uint32_t)rand(), eff, tex, mat + 0x80u, (uint32_t)rand(),
                                  (uint32_t)rand(), (uint32_t)rand() & 0xffu};
        const uint32_t count_g = slot, lods = lod ? 1u + (uint32_t)rand() % 3u : (uint32_t)rand() % 2u,
                       tag70 = rand() % 3 == 0 ? 0xffffffffu : (uint32_t)rand() % 50u;
        auto setup = [&](X86 &x) {
            memcpy(g_mem + area, init, sizeof init);
            memcpy(g_mem + STK, saved_stack, sizeof saved_stack);   // both runs start from the same stack bytes
            wr32(0x982cdcu, count_g);
            wr32(0x982cd8u, 1000u);
            wr32(0x982cd4u, 7u);
            wr32(0x8fae70u, tag70);
            wr32(0x982a20u, view);
            wr32(0x901830u, lods);
            wr32(0x919650u, mat);
            wr32(0x919690u, area + 0xa00u);
            const uint32_t thr_at[5] = {0x8fae50u, 0x8fae58u, 0x8fae5cu, 0x8fae60u, 0x8fae68u};
            for (int k = 0; k < 5; ++k)
                wrf32(thr_at[k], th[k]);
            wr32(0x982c3cu, cap);
            x = *c;
            x.r[R_ESP] = stack;
            x.r[R_EAX] = 0x0badf00du;
            x.r[R_ECX] = 0x13579bdfu;
            x.r[R_EDX] = 0x2468ac00u | ((uint32_t)rand() & 0xffu);
            x.r[R_EBX] = 0x11111111u;
            x.r[R_EBP] = 0x22222222u;
            x.r[R_ESI] = 0x33333333u;
            x.r[R_EDI] = 0x44444444u;
            wr32(stack, ret);
            for (int k = 0; k < 9; ++k)
                wr32(stack + 4u + 4u * k, args[k]);
        };
        const uint32_t ent = 0x93e878u + (slot < 0x1000u ? slot : 0u) * 0x44u, pr = pairs + (slot < 0x1000u ? slot : 0u) * 8u;
        memcpy(saved_entry, g_mem + ent, 0x44);
        memcpy(saved_pair, g_mem + pr, 8);
        X86 a, b;
        const uint32_t dl = (uint32_t)rand() & 0xffu;
        setup(a);
        a.r[R_EDX] = (a.r[R_EDX] & 0xffffff00u) | dl;
        fn_006e0ab0(&a);
        memcpy(out_area, g_mem + area, sizeof out_area);
        memcpy(out_stack, g_mem + STK, sizeof out_stack);
        memcpy(out_entry, g_mem + ent, 0x44);
        uint8_t out_pair[8];
        memcpy(out_pair, g_mem + pr, 8);
        uint32_t out_g[4];
        for (int i = 0; i < 4; ++i)
            out_g[i] = rd32(globals[i]);
        memcpy(g_mem + ent, saved_entry, 0x44);
        memcpy(g_mem + pr, saved_pair, 8);
        setup(b);
        b.r[R_EDX] = (b.r[R_EDX] & 0xffffff00u) | dl;
        if (!append(&b)) {
            ++fell_back;
            fn_006e0ab0(&b);
        }
        bool same = !memcmp(out_area, g_mem + area, sizeof out_area) &&
                    !memcmp(out_stack, g_mem + STK, sizeof out_stack) && !memcmp(out_entry, g_mem + ent, 0x44) &&
                    !memcmp(out_pair, g_mem + pr, 8);
        for (int i = 0; i < 4 && same; ++i)
            same = rd32(globals[i]) == out_g[i];
        same = same && a.r[R_EAX] == b.r[R_EAX] && a.r[R_ECX] == b.r[R_ECX] && a.r[R_EDX] == b.r[R_EDX] &&
               a.r[R_EBX] == b.r[R_EBX] && a.r[R_EBP] == b.r[R_EBP] && a.r[R_ESI] == b.r[R_ESI] &&
               a.r[R_EDI] == b.r[R_EDI] && a.r[R_ESP] == b.r[R_ESP] && a.eip == b.eip &&
               x86_get_eflags(&a) == x86_get_eflags(&b) && a.fpu_top == b.fpu_top && a.fpu_tag == b.fpu_tag &&
               a.fpu_sw == b.fpu_sw;
        for (unsigned k = 0; k < 8 && same; ++k)
            same = !memcmp(&a.st[k], &b.st[k], sizeof(double)) && a.st_bits[k] == b.st_bits[k] &&
                   a.st_exact[k] == b.st_exact[k];
        memcpy(g_mem + ent, saved_entry, 0x44);
        memcpy(g_mem + pr, saved_pair, 8);
        ++runs;
        if (!same && ++bad <= 8)
            fprintf(stderr, "native self-test 006e0ab0: run %d (slot %u, lod %d) differs (eax %08x/%08x ebx %08x/%08x "
                            "fsw %04x/%04x)\n", n, slot, (int)lod, a.r[R_EAX], b.r[R_EAX], a.r[R_EBX], b.r[R_EBX],
                    a.fpu_sw, b.fpu_sw);
    }
    for (int i = 0; i < NG; ++i)
        wr32(globals[i], saved_g[i]);
    memcpy(g_mem + area, saved_area, sizeof saved_area);
    memcpy(g_mem + STK, saved_stack, sizeof saved_stack);
    fprintf(stderr, "native self-test 006e0ab0 (render entry): %d runs, %d differ (%d ran the translation)\n", runs, bad,
            fell_back);
}

} // namespace

extern "C" void native_006e0ab0(X86 *c) {
    if (!native_on(c) || !append(c))
        fn_006e0ab0(c);
}
