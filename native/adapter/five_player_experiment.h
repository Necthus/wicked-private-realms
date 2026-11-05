/* Private opt-in prototype. Never enabled in a normal client build.
   Original globals stay at their original offsets; an extra Input is appended.
   The original allocator, clear and copy sizes are checked before enlargement. */
#define FIVE_GLOBAL_SIZE 0x8a0u
#define FIVE_INPUT_SIZE 0x110u
#define FIVE_OVERLAY_SLOTS 4096
#define FIVE_EXTENDED_SIZE (FIVE_GLOBAL_SIZE + FIVE_INPUT_SIZE + FIVE_OVERLAY_SLOTS * 48u)
static void FiveOverlaySerialize(void *globals, void *serializer);

static int FiveCapacity(void *frame) {
    void *config = frame ? *(void **)((BYTE *)frame + 0x2e0) : NULL;
    return config ? *(int *)((BYTE *)config + 0x10) : 0;
}
static BYTE *FiveInput(void *frame) {
    BYTE *globals = frame ? *(BYTE **)((BYTE *)frame + 0x2c8) : NULL;
    return globals ? globals + FIVE_GLOBAL_SIZE : NULL;
}
typedef void (__cdecl *FiveSetInput)(void *, int, void *, void *);
static FiveSetInput g_five_set_input;
static void __cdecl FiveSetInputHook(void *frame, int player, void *input, void *method) {
    if (player == 5 && FiveCapacity(frame) == 5) {
        BYTE proxy[0x2d0];
        memcpy(proxy, frame, sizeof(proxy));
        /* Preserve the game's complete button transition logic by targeting its
           first slot through a temporary frame view of the appended input. */
        *(void **)(proxy + 0x2c8) = FiveInput(frame) - 0x260;
        g_five_set_input(proxy, 1, input, method);
        static LONG observed;
        if (!InterlockedExchange(&observed, 1)) Log("five-player-input-written", 5);
        return;
    }
    g_five_set_input(frame, player, input, method);
}
typedef void *(__cdecl *FiveGetInput)(void *, int, void *);
static FiveGetInput g_five_get_input;
static void *__cdecl FiveGetInputHook(void *frame, int player, void *method) {
    if (player == 5 && FiveCapacity(frame) == 5) return FiveInput(frame);
    return g_five_get_input(frame, player, method);
}
typedef void (__cdecl *FiveGlobalsSerialize)(void *, void *, void *);
static FiveGlobalsSerialize g_five_globals_serialize;
static FiveGlobalsSerialize g_five_input_serialize;
static void __cdecl FiveGlobalsSerializeHook(void *globals, void *serializer, void *method) {
    g_five_globals_serialize(globals, serializer, method);
    void *frame = serializer ? *(void **)((BYTE *)serializer + 0x18) : NULL;
    if (FiveCapacity(frame) == 5) {
        g_five_input_serialize((BYTE *)globals + FIVE_GLOBAL_SIZE, serializer, NULL);
        FiveOverlaySerialize(globals, serializer);
    }
}
typedef void (__cdecl *FiveConnectionBits)(void *, void *, void *);
static FiveConnectionBits g_five_connection_bits;
static void __cdecl FiveConnectionBitsHook(void *frame, void *bits, void *method) {
    g_five_connection_bits(frame, bits, method);
    if (FiveCapacity(frame) == 5) *(int *)((BYTE *)bits + 8) = 5;
}
typedef void *(__cdecl *FiveConnectionGetter)(void *, void *, void *);
static FiveConnectionGetter g_five_connection_getter;
static void *__cdecl FiveConnectionGetterHook(void *output, void *frame, void *method) {
    void *result = g_five_connection_getter(output, frame, method);
    if (FiveCapacity(frame) == 5) *(int *)((BYTE *)result + 8) = 5;
    return result;
}
typedef void (__cdecl *FiveSessionCtor)(void *, void *, void *);
static FiveSessionCtor g_five_session_ctor;
static void __cdecl FiveSessionCtorHook(void *self, void *arguments, void *method) {
    /* The game's session creation has already deep-cloned this configuration.
       Change only multiplayer sessions, before Quantum allocates frame inputs. */
    if (arguments && *(int *)arguments == 0) {
        void *config = *(void **)((BYTE *)arguments + 8);
        if (!config) { Fatal(303, "five-player-session-config-missing"); return; }
        *(int *)((BYTE *)config + 0x10) = 5;
        Log("five-player-session-configured", 5);
    }
    g_five_session_ctor(self, arguments, method);
}
static BOOL FivePatchSize(size_t rva) {
    uint32_t *address = (uint32_t *)(g_image + rva);
    if (*address != FIVE_GLOBAL_SIZE) { Log("five-player-layout-rejected", (LONG)rva); return FALSE; }
    DWORD old;
    if (!VirtualProtect(address, 4, PAGE_EXECUTE_READWRITE, &old)) return FALSE;
    *address = FIVE_EXTENDED_SIZE;
    FlushInstructionCache(GetCurrentProcess(), address, 4);
    VirtualProtect(address, 4, old, &old);
    return TRUE;
}
static BOOL FivePatchByte(size_t rva, BYTE expected, BYTE value) {
    BYTE *address = g_image + rva;
    if (*address != expected) return FALSE;
    DWORD old;
    if (!VirtualProtect(address, 1, PAGE_EXECUTE_READWRITE, &old)) return FALSE;
    *address = value;
    FlushInstructionCache(GetCurrentProcess(), address, 1);
    VirtualProtect(address, 1, old, &old);
    return TRUE;
}
static BOOL FivePatchCapacity(size_t rva) {
    uint32_t *address = (uint32_t *)(g_image + rva);
    if (*address != 4) return FALSE;
    DWORD old;
    if (!VirtualProtect(address, 4, PAGE_EXECUTE_READWRITE, &old)) return FALSE;
    *address = 5;
    FlushInstructionCache(GetCurrentProcess(), address, 4);
    VirtualProtect(address, 4, old, &old);
    return TRUE;
}
typedef void *(__cdecl *FiveArrayNew)(void *, size_t);
typedef void (__cdecl *FiveBarrier)(void *, void **, void *);
static FiveArrayNew g_five_array_new;
static FiveBarrier g_five_barrier;
typedef void (__cdecl *FiveArrayCopy)(void *, void *, int, void *);
typedef void (__cdecl *FivePrepareSave)(void *, void *, uint8_t, void *, void *);
static FiveArrayCopy g_five_array_copy;
static FivePrepareSave g_five_prepare_save;
static void __cdecl FivePrepareSaveHook(void *self, void *bundle, uint8_t final_save, void *info, void *method) {
    BYTE *characters = bundle ? *(BYTE **)((BYTE *)bundle + 0x68) : NULL;
    uintptr_t handle = 0;
    if (characters && *(size_t *)(characters + 0x18) == 4) {
        /* Local four-slot sessions still use their original simulation config.
           Pad their save container with an empty fifth slot for the experimental
           save worker; the game's array copier handles all managed references. */
        void *expanded = g_five_array_new(*(void **)characters, 5);
        if (!expanded) {Fatal(306, "five-player-save-allocation-failed");return;}
        handle = g_gc_new(expanded, 0);
        g_five_array_copy(characters, expanded, 4, NULL);
        *(void **)((BYTE *)bundle + 0x68) = expanded;
    }
    g_five_prepare_save(self, bundle, final_save, info, method);
    if (handle) g_gc_free(handle);
}
static void FiveExpandLocations(void *state) {
    static const size_t strides[] = {8,8,8,8,8,24,32,1,4,4};
    if (!state) return;
    for (int i = 0; i < 10; ++i) {
        void **field = (void **)((BYTE *)state + 0x10 + i * 8);
        BYTE *array = (BYTE *)*field;
        if (!array || *(size_t *)(array + 0x18) != 4) continue;
        BYTE *expanded = (BYTE *)g_five_array_new(*(void **)array, 5);
        if (!expanded) { Fatal(305, "five-player-location-allocation-failed"); return; }
        memcpy(expanded + 0x20, array + 0x20, 4 * strides[i]);
        g_five_barrier(state, field, expanded);
    }
}
typedef void (__cdecl *FiveLocationTick)(void *, void *);
static FiveLocationTick g_five_location_tick;
static void __cdecl FiveLocationTickHook(void *frame, void *method) {
    if (frame) FiveExpandLocations(*(void **)((BYTE *)frame + 0x2b0));
    g_five_location_tick(frame, method);
}
typedef uint8_t (__cdecl *FiveLocationQuery)(void *, int, void *, void *);
static FiveLocationQuery g_five_location_query;
static uint8_t __cdecl FiveLocationQueryHook(void *self, int player, void *output, void *method) {
    void *frame = self ? *(void **)((BYTE *)self + 0x10) : NULL;
    if (frame) FiveExpandLocations(*(void **)((BYTE *)frame + 0x2b0));
    return g_five_location_query(self, player, output, method);
}
#include "five_player_overlay.h"

static BOOL FiveInstall(void) {
    if (!(g_config->reserved_flags & 2)) return TRUE;
    if (!FivePatchSize(0x5969cde) || !FivePatchSize(0x5969cfa) || !FivePatchSize(0x596eb86)) return FALSE;
    if (!FivePatchByte(0x5bf5dde, 4, 5)) return FALSE; /* QuantumGame.AssignSession upper bound. */
    const size_t location_arrays[] = {0x87164de,0x87164ec,0x8716501,0x8716516,0x871652b,
                                     0x8716540,0x8716555,0x871656a,0x871657f,0x8716594};
    for (int i = 0; i < 10; ++i) if (!FivePatchCapacity(location_arrays[i])) return FALSE;
    if (!FivePatchByte(0x8717bf9, 4, 5) || !FivePatchByte(0x8717cae, 4, 5)) return FALSE;
    if (!FivePatchByte(0x5950f12, 4, 5) || !FivePatchByte(0x5950fef, 4, 5) ||
        !FivePatchByte(0x59516a2, 4, 5) || !FivePatchCapacity(0x5950f2d) || !FivePatchCapacity(0x5951610)) return FALSE;
    if (!FivePatchByte(0x5be2e0a, 16, 20) || !FivePatchByte(0x5be31ab, 16, 20) || !FivePatchByte(0x5be2eaf, 4, 5)) return FALSE;
    g_five_input_serialize = (FiveGlobalsSerialize)(g_image + 0x602aa90);
    g_five_array_new = (FiveArrayNew)GetProcAddress((HMODULE)g_image, "il2cpp_array_new_specific");
    g_five_barrier = (FiveBarrier)GetProcAddress((HMODULE)g_image, "il2cpp_gc_wbarrier_set_field");
    if (!g_five_array_new || !g_five_barrier) return FALSE;
    g_five_array_copy = (FiveArrayCopy)(g_image + 0x6e89b50);
    if (!AddHook(0x5967d60, FiveSetInputHook, (void **)&g_five_set_input) ||
        !AddHook(0x5977890, FiveGetInputHook, (void **)&g_five_get_input) ||
        !AddHook(0x5edd570, FiveGlobalsSerializeHook, (void **)&g_five_globals_serialize) ||
        !AddHook(0x5967d10, FiveConnectionBitsHook, (void **)&g_five_connection_bits) ||
        !AddHook(0x59696b0, FiveConnectionGetterHook, (void **)&g_five_connection_getter) ||
        !AddHook(0x8717a00, FiveLocationTickHook, (void **)&g_five_location_tick) ||
        !AddHook(0x8716b50, FiveLocationQueryHook, (void **)&g_five_location_query) ||
        !AddHook(0x5954770, FivePrepareSaveHook, (void **)&g_five_prepare_save) ||
        !AddHook(0x70dbe40, FiveSessionCtorHook, (void **)&g_five_session_ctor)) return FALSE;
    if (!FiveOverlayInstall()) return FALSE;
    Log("five-player-experiment-installed", 5);
    return TRUE;
}
