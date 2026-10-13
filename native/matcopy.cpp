/* matcopy.cpp - 00444fe0, copy a 4x4 float matrix (64 bytes), native (R3 run 31, owner 12 Oct: "now optimize the top
 * game functions natively"; review/run31/profile: 0.10 ms per frame of game-thread self time in the cruise).
 *
 * cdecl (dst, src), RET. The original moves the 16 words through EDX/ECX (12 of them) and the x87 stack (4 pairs: FLD
 * float, FLD float, FXCH, FSTP, FSTP), and parks src[3], src[7], src[11], src[15] in its own argument slot [ESP+8] on
 * the way. Here the words are copied as they are: a float that goes through FLD/FSTP comes back with the same bits
 * unless it is a signalling NaN (the load quiets it), so a call whose x87 words include one runs the translation.
 * Left as the original leaves them: EAX = dst, ECX = src[12], EDX = src[15], [ESP+8] = src[15], and the two x87 slots
 * below the top hold the last pair (ST at top-2 = src[13], top-1 = src[14], both empty, no exact bits); status, control
 * and the stack top are unchanged (no instruction here sets a status bit for a non-NaN value). The translation also
 * runs when the matrices overlap each other or the call's own stack words (the original's order would then matter),
 * or when either x87 slot it borrows was in use.
 *
 * RECOMP_NATIVE=0 (all natives) or RECOMP_OPT_NATIVE_MATCOPY=0 (this one) run the translation; RECOMP_NATIVE_SELFTEST=1
 * compares both on random matrices (signalling and quiet NaNs, infinities, denormals included) at the first call. */
#include "runtime/guest.h"
extern "C" {
#include "funcs.h"
}
#include "runtime/memory.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>

extern "C" void native_00444fe0(X86 *c);

namespace {

int g_native = -1;   // 1 native, 0 translated; -1 until the environment has been read
void selftest(X86 *c);

int native_on(X86 *c) {
    if (__builtin_expect(g_native >= 0, 1))
        return g_native;
    const char *e = getenv("RECOMP_NATIVE"), *k = getenv("RECOMP_OPT_NATIVE_MATCOPY");
    const int want = !(e && e[0] == '0') && !(k && k[0] == '0');
    g_native = 0;   // the self-test's translated half must not recurse into the native one
    const char *t = getenv("RECOMP_NATIVE_SELFTEST");
    if (t && t[0] == '1')
        selftest(c);
    g_native = want;
    return g_native;
}

bool snan(uint32_t u) {
    return (u & 0x7f800000u) == 0x7f800000u && (u & 0x007fffffu) != 0 && (u & 0x00400000u) == 0;
}
bool overlap(uint32_t a, uint32_t an, uint32_t b, uint32_t bn) {
    return a < b + bn && b < a + an;
}
float bits_f(uint32_t u) {
    float f;
    memcpy(&f, &u, 4);
    return f;
}

// The 4 words of each row that the original carries through the x87 stack.
constexpr unsigned kFpu[8] = {1, 2, 5, 6, 9, 10, 13, 14};

// true: done natively; false: the caller runs the translation (nothing was written).
bool matcopy(X86 *c) {
    const uint32_t esp = c->r[R_ESP];
    if (!gm_valid(esp, 12))
        return false;
    const uint32_t dst = rd32(esp + 4), src = rd32(esp + 8);
    if (!gm_valid(src, 64) || !gm_valid(dst, 64))
        return false;
    if ((dst != src && overlap(dst, 64, src, 64)) || overlap(dst, 64, esp, 12) || overlap(src, 64, esp, 12))
        return false;
    const unsigned s1 = (c->fpu_top - 1u) & 7u, s2 = (c->fpu_top - 2u) & 7u;
    if (ftag_of(c, s1) != FTAG_EMPTY || ftag_of(c, s2) != FTAG_EMPTY)
        return false;
    uint32_t w[16];
    for (unsigned i = 0; i < 16; ++i)
        w[i] = rd32(src + 4 * i);
    for (unsigned j = 0; j < 8; ++j)
        if (snan(w[kFpu[j]]))
            return false;
    for (unsigned i = 0; i < 16; ++i)
        wr32(dst + 4 * i, w[i]);
    wr32(esp + 8, w[15]);
    // the x87 slots the last FLD/FLD/FXCH/FSTP/FSTP left behind (values kept, tags empty, no exact bits)
    c->st[s2] = (double)bits_f(w[13]);
    c->st[s1] = (double)bits_f(w[14]);
    c->st_bits[s1] = c->st_bits[s2] = 0;
    c->st_exact[s1] = c->st_exact[s2] = 0;
    c->r[R_EAX] = dst;
    c->r[R_ECX] = w[12];
    c->r[R_EDX] = w[15];
    c->eip = rd32(esp);   // RET
    c->r[R_ESP] = esp + 4;
    return true;
}

uint32_t rnd_word(unsigned i) {
    static const uint32_t edge[] = {0x00000000u, 0x80000000u, 0x3f800000u, 0x7f800000u, 0xff800000u, 0x7fc00000u,
                                    0xffc00001u, 0x7f800001u, 0x7fa00000u, 0x00000001u, 0x807fffffu, 0x7f7fffffu};
    const unsigned r = (unsigned)rand();
    if ((r & 7u) == 0)
        return edge[(r >> 3) % (sizeof edge / sizeof edge[0])];
    return ((uint32_t)rand() << 16) ^ (uint32_t)rand() ^ i;
}

void selftest(X86 *c) {
    const uint32_t stack = c->r[R_ESP] - 0x20000;
    const uint32_t area = stack - 0x1000;   // two matrices below the scratch stack
    uint32_t ret = 0x00401001u;   // not a function entry (the translated RET tail-calls an entry: culling.c note)
    while (recomp_index_of(ret) >= 0 || recomp_module_lookup(ret) >= 0)
        ++ret;
    int bad = 0, runs = 0, fell_back = 0;
    for (int n = 0; n < 4000; ++n) {
        const uint32_t src = area, dst = (n % 50 == 0) ? area : area + 0x100;   // every 50th call in place
        uint32_t m[16];
        for (unsigned i = 0; i < 16; ++i) {
            m[i] = rnd_word(i);
            wr32(src + 4 * i, m[i]);
            if (dst != src)
                wr32(dst + 4 * i, 0xdeadbeefu);
        }
        X86 a = *c;
        a.r[R_ESP] = stack;
        a.r[R_ECX] = 0x13579bdfu;
        a.r[R_EDX] = 0x2468ace0u;
        a.r[R_EAX] = 0x0badf00du;
        if (n % 97 == 0)
            ftag_put(&a, (a.fpu_top - 1u) & 7u, 0);   // a borrowed slot in use: the native must decline
        wr32(stack, ret);
        wr32(stack + 4, dst);
        wr32(stack + 8, src);
        X86 b = a;
        uint8_t mem_a[64], mem_b[64];
        uint32_t arg_a, arg_b;
        fn_00444fe0(&a);
        memcpy(mem_a, g_mem + dst, 64);
        arg_a = rd32(stack + 8);
        // reset the inputs, then the native
        for (unsigned i = 0; i < 16; ++i) {
            wr32(src + 4 * i, m[i]);
            if (dst != src)
                wr32(dst + 4 * i, 0xdeadbeefu);
        }
        wr32(stack, ret);
        wr32(stack + 4, dst);
        wr32(stack + 8, src);
        if (!matcopy(&b)) {
            ++fell_back;
            fn_00444fe0(&b);
        }
        memcpy(mem_b, g_mem + dst, 64);
        arg_b = rd32(stack + 8);
        bool same = !memcmp(mem_a, mem_b, 64) && arg_a == arg_b && a.r[R_EAX] == b.r[R_EAX] &&
                    a.r[R_ECX] == b.r[R_ECX] && a.r[R_EDX] == b.r[R_EDX] && a.r[R_ESP] == b.r[R_ESP] &&
                    a.r[R_EBX] == b.r[R_EBX] && a.r[R_ESI] == b.r[R_ESI] && a.r[R_EDI] == b.r[R_EDI] &&
                    a.r[R_EBP] == b.r[R_EBP] && a.eip == b.eip && a.fpu_top == b.fpu_top && a.fpu_tag == b.fpu_tag &&
                    a.fpu_sw == b.fpu_sw && a.fpu_cw == b.fpu_cw;
        for (unsigned k = 0; k < 8 && same; ++k)
            same = !memcmp(&a.st[k], &b.st[k], sizeof(double)) && a.st_bits[k] == b.st_bits[k] &&
                   a.st_exact[k] == b.st_exact[k];
        ++runs;
        if (!same && ++bad <= 8)
            fprintf(stderr, "native self-test 00444fe0: run %d differs (eax %08x/%08x ecx %08x/%08x edx %08x/%08x arg "
                            "%08x/%08x mem %d)\n", n, a.r[R_EAX], b.r[R_EAX], a.r[R_ECX], b.r[R_ECX], a.r[R_EDX],
                    b.r[R_EDX], arg_a, arg_b, memcmp(mem_a, mem_b, 64) != 0);
    }
    fprintf(stderr, "native self-test 00444fe0 (matrix copy): %d runs, %d differ (%d ran the translation)\n", runs, bad,
            fell_back);
}

} // namespace

extern "C" void native_00444fe0(X86 *c) {
    if (!native_on(c) || !matcopy(c))
        fn_00444fe0(c);
}
