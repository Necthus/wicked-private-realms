/* Frame-owned fifth-player component fields. This experiment preserves original
   component layouts; inlined array bounds checks are redirected at verified sites. */
#include <stdlib.h>
#ifndef FIVE_OVERLAY_SLOTS
#define FIVE_OVERLAY_SLOTS 4096
#endif
#define FIVE_OVERLAY_RECORD_SIZE 48

typedef struct FiveOverlayRecord {
    uint64_t entity;
    uint32_t kind, size;
    BYTE data[32];
} FiveOverlayRecord;
C_ASSERT(sizeof(FiveOverlayRecord) == FIVE_OVERLAY_RECORD_SIZE);
static DWORD g_five_frame_tls = TLS_OUT_OF_INDEXES;
static SRWLOCK g_five_overlay_lock = SRWLOCK_INIT;
static FiveOverlayRecord *FiveRecords(void *frame) {
    return (FiveOverlayRecord *)(FiveInput(frame) + FIVE_INPUT_SIZE);
}
static BOOL FiveDataNonzero(const FiveOverlayRecord *record) {
    for (int i = 0; i < 32; ++i) if (record->data[i]) return TRUE;
    return FALSE;
}
static FiveOverlayRecord *FiveFindRecord(void *frame, uint64_t entity, uint32_t kind, uint32_t size) {
    FiveOverlayRecord *records = FiveRecords(frame), *free_entry = NULL;
    for (int i = 0; i < FIVE_OVERLAY_SLOTS; ++i) {
        if (records[i].entity == entity && records[i].kind == kind) return &records[i];
        if (!records[i].entity && !free_entry) free_entry = &records[i];
    }
    FiveOverlayRecord *entry = free_entry; /* Returned native references cannot be evicted while live. */
    if (!entry) { Fatal(310, "five-player-component-cache-full"); return NULL; }
    ZeroMemory(entry, sizeof(*entry));entry->entity = entity;entry->kind = kind;entry->size = size;
    return entry;
}
static BYTE *FiveComponentField(void *frame, BYTE *array, int field, int stride, int component_size) {
    BYTE *component = array - field;
    BYTE *buffers = *(BYTE **)((BYTE *)frame + 0xd0);
    int count = *(int *)((BYTE *)frame + 0xd8);
    if (!buffers || count <= 0 || count > 8192 || stride <= 0 || stride > 32) return NULL;
    for (int i = 0; i < count; ++i) {
        BYTE *buffer = buffers + i * 0x48;
        if (*(int *)(buffer + 0xc) != component_size) continue;
        BYTE *blocks = *(BYTE **)(buffer + 0x18);
        int blocks_count = *(int *)(buffer + 0x24), capacity = *(int *)(buffer + 0x28);
        if (!blocks || blocks_count < 0 || blocks_count > 4096 || capacity <= 0) continue;
        for (int b = 0; b < blocks_count; ++b) {
            uint64_t *handles = *(uint64_t **)(blocks + b * 16);
            BYTE *data = *(BYTE **)(blocks + b * 16 + 8);
            if (!handles || !data || component < data || component >= data + (size_t)capacity * component_size) continue;
            size_t offset = (size_t)(component - data);
            if (offset % component_size) continue;
            uint64_t entity = handles[offset / component_size];
            if (!entity) return NULL;
            int canonical = component_size == 168 && field == 0x18 ? 4 : field;
            int data_offset = component_size == 168 && field == 0x18 ? 0x14 : 0;
            uint32_t kind = ((uint32_t)*(int *)(buffer + 0x14) << 16) | (uint32_t)canonical;
            AcquireSRWLockExclusive(&g_five_overlay_lock);
            FiveOverlayRecord *record = FiveFindRecord(frame, entity, kind, component_size == 168 && canonical == 4 ? 32 : stride);
            ReleaseSRWLockExclusive(&g_five_overlay_lock);
            return record ? record->data + data_offset : NULL;
        }
    }
    return NULL;
}

typedef void (__cdecl *FiveSimulate)(void *, void *, void *);
static FiveSimulate g_five_simulate;
static void __cdecl FiveSimulateHook(void *self, void *frame, void *method) {
    void *previous = TlsGetValue(g_five_frame_tls);TlsSetValue(g_five_frame_tls, frame);
    g_five_simulate(self, frame, method);TlsSetValue(g_five_frame_tls, previous);
}
typedef void *(__cdecl *FiveViewFrame)(void *, void *);
static FiveViewFrame g_five_view_frame, g_five_verified_frame;
static void *__cdecl FiveViewFrameHook(void *self, void *method) {
    void *frame = g_five_view_frame(self, method);TlsSetValue(g_five_frame_tls, frame);return frame;
}
static void *__cdecl FiveVerifiedFrameHook(void *self, void *method) {
    void *frame = g_five_verified_frame(self, method);TlsSetValue(g_five_frame_tls, frame);return frame;
}
typedef void (__cdecl *FiveEntityTick)(void *, void *, void *, float, void *);
static FiveEntityTick g_five_entity_tick;
static void __cdecl FiveEntityTickHook(void *self, void *view, void *frame, float dt, void *method) {
    void *previous = TlsGetValue(g_five_frame_tls);TlsSetValue(g_five_frame_tls, frame);
    g_five_entity_tick(self, view, frame, dt, method);TlsSetValue(g_five_frame_tls, previous);
}

typedef void *(__cdecl *FiveClassMethods)(void *, void **);
typedef const char *(__cdecl *FiveMethodName)(void *);
typedef uint32_t (__cdecl *FiveParameterCount)(void *);
typedef void *(__cdecl *FiveParameter)(void *, uint32_t);
typedef char *(__cdecl *FiveTypeName)(void *);
typedef void (__cdecl *FiveFree)(void *);
typedef void (__cdecl *FiveSerializeInt)(void *, int *, void *);
static FiveClassMethods g_five_class_methods;
static FiveMethodName g_five_method_name;
static FiveParameterCount g_five_parameter_count;
static FiveParameter g_five_parameter;
static FiveTypeName g_five_type_name;
static FiveFree g_five_free;
typedef struct FiveStreamMethod {void *klass, *method;FiveSerializeInt function;} FiveStreamMethod;
static FiveStreamMethod g_five_stream_methods[16];
static SRWLOCK g_five_stream_lock = SRWLOCK_INIT;
static FiveStreamMethod FiveResolveStream(void *stream) {
    void *klass = *(void **)stream;
    FiveStreamMethod result = {0};
    AcquireSRWLockExclusive(&g_five_stream_lock);
    int free_slot = -1;
    for (int i = 0; i < 16; ++i) {
        if (g_five_stream_methods[i].klass == klass) {result = g_five_stream_methods[i];goto done;}
        if (!g_five_stream_methods[i].klass && free_slot < 0) free_slot = i;
    }
    void *iterator = NULL, *method;
    while ((method = g_five_class_methods(klass, &iterator)) != NULL) {
        const char *name = g_five_method_name(method);
        if (strcmp(name, "Serialize") || g_five_parameter_count(method) != 1) continue;
        char *type = g_five_type_name(g_five_parameter(method, 0));
        BOOL match = type && !strcmp(type, "System.Int32&");
        if (type) g_five_free(type);
        if (match) {result.klass = klass;result.method = method;result.function = *(FiveSerializeInt *)method;break;}
    }
    if (result.function && free_slot >= 0) g_five_stream_methods[free_slot] = result;
done:
    ReleaseSRWLockExclusive(&g_five_stream_lock);
    return result;
}
static int FiveRecordOrder(const void *a, const void *b) {
    const FiveOverlayRecord *left = (const FiveOverlayRecord *)a, *right = (const FiveOverlayRecord *)b;
    if (left->entity != right->entity) return left->entity < right->entity ? -1 : 1;
    return left->kind < right->kind ? -1 : left->kind > right->kind;
}
static void FiveOverlaySerialize(void *globals, void *serializer) {
    void *stream = *(void **)((BYTE *)serializer + 0x28);
    FiveStreamMethod method = FiveResolveStream(stream);
    if (!method.function) {Fatal(311, "five-player-snapshot-stream-unsupported");return;}
    BOOL writing = ((uint8_t (__cdecl *)(void *, void *))(g_image + 0x71229b0))(serializer, NULL);
    FiveOverlayRecord snapshot[FIVE_OVERLAY_SLOTS];int count = 0;
    FiveOverlayRecord *records = (FiveOverlayRecord *)((BYTE *)globals + FIVE_GLOBAL_SIZE + FIVE_INPUT_SIZE);
    if (writing) {
        AcquireSRWLockShared(&g_five_overlay_lock);
        for (int i = 0; i < FIVE_OVERLAY_SLOTS; ++i) if (records[i].entity && FiveDataNonzero(&records[i])) snapshot[count++] = records[i];
        ReleaseSRWLockShared(&g_five_overlay_lock);
        qsort(snapshot, count, sizeof(snapshot[0]), FiveRecordOrder);
    }
    method.function(stream, &count, method.method);
    if (count < 0 || count > FIVE_OVERLAY_SLOTS) {Fatal(312, "five-player-snapshot-record-count-invalid");return;}
    if (!writing) ZeroMemory(snapshot, sizeof(snapshot));
    for (int i = 0; i < count; ++i) {
        int *words = (int *)&snapshot[i];
        for (int j = 0; j < sizeof(snapshot[i])/4; ++j) method.function(stream, &words[j], method.method);
        if (!snapshot[i].entity || !snapshot[i].size || snapshot[i].size > 32) {Fatal(313, "five-player-snapshot-record-invalid");return;}
    }
    if (!writing) {
        AcquireSRWLockExclusive(&g_five_overlay_lock);
        ZeroMemory(records, FIVE_OVERLAY_SLOTS * sizeof(records[0]));
        memcpy(records, snapshot, count * sizeof(records[0]));
        ReleaseSRWLockExclusive(&g_five_overlay_lock);
    }
}

typedef struct FiveMidRegisters { uint64_t r[15], flags; } FiveMidRegisters;
static uint64_t *FiveRegister(FiveMidRegisters *context, int reg) {
    static const int index[16] = {14,13,12,11,-1,10,9,8,7,6,5,4,3,2,1,0};
    return reg >= 0 && reg < 16 && index[reg] >= 0 ? &context->r[index[reg]] : NULL;
}
typedef struct FiveMidSite {
    uint32_t rva, next, error;
    int index_reg, base_reg, field, stride, component_size;
    int register_compare, right_reg, clock_reg, clock_field;
} FiveMidSite;
static uint64_t FiveCompareFlags(uint32_t lhs, uint32_t rhs) {
    uint32_t result = lhs - rhs, byte = result & 255;
    byte ^= byte >> 4;byte ^= byte >> 2;byte ^= byte >> 1;
    return (lhs < rhs ? 1 : 0) | (!(byte & 1) ? 4 : 0) | ((lhs ^ rhs ^ result) & 16) |
        (!result ? 64 : 0) | ((result >> 24) & 128) | (((lhs ^ rhs) & (lhs ^ result) & 0x80000000u) ? 2048 : 0);
}
static void *FiveMidDispatch(FiveMidRegisters *context, FiveMidSite *site) {
    uint64_t *index = FiveRegister(context, site->index_reg), *base = FiveRegister(context, site->base_reg);
    uint32_t value = (uint32_t)*index;
    uint32_t limit = site->register_compare ? (uint32_t)*FiveRegister(context, site->right_reg) : 4;
    if (value == 4 && limit == 4) {
        void *frame = TlsGetValue(g_five_frame_tls);
        if (FiveCapacity(frame) == 5) {
            BYTE *extra = FiveComponentField(frame, (BYTE *)*base, site->field, site->stride, site->component_size);
            if (extra) {
                if (site->clock_reg) {
                    BYTE *component = (BYTE *)*base - site->field;
                    BYTE *clock = FiveComponentField(frame, component + site->clock_field, site->clock_field, 4, site->component_size);
                    if (!clock) {Fatal(314, "five-player-cinematic-clock-missing");return NULL;}
                    *FiveRegister(context, site->clock_reg) = (uint64_t)clock;
                }
                *base = (uint64_t)(extra - 4 * site->stride);
                context->flags = (context->flags & ~0x8d5ull) | FiveCompareFlags(value, 5);
                return g_image + site->next;
            }
            Log("five-player-component-lookup-failed", site->rva);
        } else Log("five-player-component-frame-missing", site->rva);
    }
    context->flags = (context->flags & ~0x8d5ull) | FiveCompareFlags(value, limit);
    return g_image + (value < limit ? site->next : site->error);
}
static BYTE *g_five_stub_pool;
static size_t g_five_stub_used;
static void FiveEmit8(BYTE **p, BYTE v) { *(*p)++ = v; }
static void FiveEmit32(BYTE **p, uint32_t v) { memcpy(*p, &v, 4);*p += 4; }
static void FiveEmit64(BYTE **p, uint64_t v) { memcpy(*p, &v, 8);*p += 8; }
static BYTE *FiveAllocateStub(void) {
    if (!g_five_stub_pool) {
        uintptr_t start = ((uintptr_t)g_image + g_config->image_size + 0x100ffff) & ~(uintptr_t)0xffff;
        for (size_t offset = 0; offset < 0x30000000; offset += 0x10000) {
            BYTE *block = (BYTE *)VirtualAlloc((void *)(start + offset), 0x10000, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
            if (block) {g_five_stub_pool = block;break;}
        }
    }
    if (!g_five_stub_pool || g_five_stub_used + 1024 > 0x10000) return NULL;
    BYTE *result = g_five_stub_pool + g_five_stub_used;g_five_stub_used += 1024;return result;
}
static BYTE *FiveBuildRegisterStub(void *site, void *dispatcher) {
    BYTE *stub = FiveAllocateStub(), *p = stub;
    if (!stub) return NULL;
    FiveEmit8(&p, 0x9c); /* flags */
    const BYTE pushes[] = {0x50,0x51,0x52,0x53,0x55,0x56,0x57};
    for (int i = 0; i < 7; ++i) FiveEmit8(&p, pushes[i]);
    for (int i = 0; i < 8; ++i) {FiveEmit8(&p, 0x41);FiveEmit8(&p, (BYTE)(0x50 + i));}
    FiveEmit8(&p, 0x48);FiveEmit8(&p, 0x81);FiveEmit8(&p, 0xec);FiveEmit32(&p, 0x120);
    for (int i = 0; i < 16; ++i) {
        FiveEmit8(&p, 0xf3);if (i >= 8) FiveEmit8(&p, 0x44);
        FiveEmit8(&p, 0x0f);FiveEmit8(&p, 0x7f);FiveEmit8(&p, (BYTE)(0x84 + (i & 7) * 8));FiveEmit8(&p, 0x24);FiveEmit32(&p, 32 + 16 * i);
    }
    const BYTE lea[] = {0x48,0x8d,0x8c,0x24};memcpy(p, lea, sizeof(lea));p += sizeof(lea);FiveEmit32(&p, 0x120);
    FiveEmit8(&p, 0x48);FiveEmit8(&p, 0xba);FiveEmit64(&p, (uint64_t)site);
    FiveEmit8(&p, 0x48);FiveEmit8(&p, 0xb8);FiveEmit64(&p, (uint64_t)dispatcher);FiveEmit8(&p, 0xff);FiveEmit8(&p, 0xd0);
    const BYTE store[] = {0x48,0x89,0x44,0x24,0x10};memcpy(p, store, sizeof(store));p += sizeof(store);
    for (int i = 0; i < 16; ++i) {
        FiveEmit8(&p, 0xf3);if (i >= 8) FiveEmit8(&p, 0x44);
        FiveEmit8(&p, 0x0f);FiveEmit8(&p, 0x6f);FiveEmit8(&p, (BYTE)(0x84 + (i & 7) * 8));FiveEmit8(&p, 0x24);FiveEmit32(&p, 32 + 16 * i);
    }
    FiveEmit8(&p, 0x48);FiveEmit8(&p, 0x81);FiveEmit8(&p, 0xc4);FiveEmit32(&p, 0x120);
    for (int i = 7; i >= 0; --i) {FiveEmit8(&p, 0x41);FiveEmit8(&p, (BYTE)(0x58 + i));}
    const BYTE pops[] = {0x5f,0x5e,0x5d,0x5b,0x5a,0x59,0x58,0x9d};memcpy(p, pops, sizeof(pops));p += sizeof(pops);
    FiveEmit8(&p, 0xff);FiveEmit8(&p, 0xa4);FiveEmit8(&p, 0x24);FiveEmit32(&p, (uint32_t)-400);
    FlushInstructionCache(GetCurrentProcess(), stub, p - stub);
    return stub;
}
static BOOL FiveInstallMid(FiveMidSite *site) {
    BYTE *target = g_image + site->rva;
    int prefix = (target[0] & 0xf0) == 0x40 ? 1 : 0;
    int rex = prefix ? target[0] : 0;
    int compare_length;
    if (site->register_compare) {
        if (target[prefix] != 0x3b || (target[prefix + 1] & 0xc0) != 0xc0) return FALSE;
        int left = ((target[prefix + 1] >> 3) & 7) + ((rex & 4) ? 8 : 0);
        int right = (target[prefix + 1] & 7) + ((rex & 1) ? 8 : 0);
        if (left != site->index_reg || right != site->right_reg) return FALSE;
        compare_length = prefix + 2;
    } else {
        if (site->index_reg == 4 || site->index_reg < 0 || site->index_reg > 15 ||
            (site->index_reg >= 8) != ((rex & 1) != 0) || target[prefix] != 0x83 ||
            target[prefix + 1] != 0xf8 + (site->index_reg & 7) || target[prefix + 2] != 4) return FALSE;
        compare_length = prefix + 3;
    }
    int length, displacement;
    if (target[compare_length] == 0x73) {length = compare_length + 2;displacement = (int8_t)target[compare_length + 1];}
    else if (target[compare_length] == 0x0f && target[compare_length + 1] == 0x83) {
        length = compare_length + 6;memcpy(&displacement, target + compare_length + 2, 4);
    } else return FALSE;
    if ((uint32_t)(site->rva + length + displacement) != site->error) return FALSE;
    if (site->next != site->rva + length) return FALSE;
    BYTE *stub = FiveBuildRegisterStub(site, FiveMidDispatch);
    if (!stub || (intptr_t)(stub - target - 5) != (int32_t)(stub - target - 5)) return FALSE;
    DWORD old;if (!VirtualProtect(target, length, PAGE_EXECUTE_READWRITE, &old)) return FALSE;
    target[0] = 0xe9;int32_t delta = (int32_t)(stub - target - 5);memcpy(target + 1, &delta, 4);
    for (int i = 5; i < length; ++i) target[i] = 0x90;
    FlushInstructionCache(GetCurrentProcess(), target, length);VirtualProtect(target, length, old, &old);
    return TRUE;
}
typedef struct FiveRawSite {
    uint32_t rva, length;
    int index_reg, base_reg, field, value_reg;
    uint32_t immediate;
    int preload_frame_number;
    void *original;
    BYTE expected[12];
} FiveRawSite;
static void *FiveRawDispatch(FiveMidRegisters *context, FiveRawSite *site) {
    uint32_t index = (uint32_t)*FiveRegister(context, site->index_reg);
    void *frame = TlsGetValue(g_five_frame_tls);
    if (FiveCapacity(frame) == 5) {
        if (index >= 5) {Fatal(315, "five-player-clock-index-invalid");return NULL;}
        if (index == 4) {
            BYTE *component = (BYTE *)*FiveRegister(context, site->base_reg);
            BYTE *field = FiveComponentField(frame, component + site->field, site->field, 4, 168);
            if (!field) {Fatal(316, "five-player-clock-owner-missing");return NULL;}
            if (site->preload_frame_number) *FiveRegister(context, 0) = *(uint32_t *)((BYTE *)*FiveRegister(context, 2) + 0x6ac);
            uint32_t value = site->value_reg < 0 ? site->immediate : (uint32_t)*FiveRegister(context, site->value_reg);
            memcpy(field, &value, 4);
            return g_image + site->rva + site->length;
        }
    }
    return site->original;
}
static BOOL FiveInstallRaw(FiveRawSite *site) {
    BYTE *target = g_image + site->rva;
    if (site->length < 5 || site->length > sizeof(site->expected) || memcmp(target, site->expected, site->length)) return FALSE;
    BYTE *stub = FiveBuildRegisterStub(site, FiveRawDispatch);
    return stub && MH_CreateHook(target, stub, &site->original) == MH_OK;
}
static FiveRawSite g_five_raw_sites[] = {
    {0x5be3c5c,8,1,7,0x18,-1,0xffffffffu,0,NULL,{0xc7,0x44,0x8f,0x18,0xff,0xff,0xff,0xff}},
    {0x5be3c7f,10,1,7,4,0,0,1,NULL,{0x8b,0x82,0xac,0x06,0x00,0x00,0x89,0x44,0x8f,0x04}},
    {0x5be434a,5,1,14,4,0,0,0,NULL,{0x41,0x89,0x44,0x8e,0x04}},
};
static FiveMidSite g_five_mid_sites[] = {
    {0x5be2e5c,0x5be2e64,0x5be3872,6,1,0x28,4,168,1,0,15,0x18},
    {0x5be3202,0x5be320b,0x5be38ea,14,1,0x28,4,168,1,0,15,4},
    {0x5b7a1f9,0x5b7a202,0x5b7a3bc,0,2,0xc,4,120}, /* Quantum.TriggerSystem$$TriggerPlayerEvents */
    {0x5be3b4e,0x5be3b57,0x5be3e32,0,2,0x28,4,168}, /* Quantum.NanoCinematicSystem$$StopCleanly */
    {0x5be3b9c,0x5be3ba5,0x5be3dd8,0,2,0x28,4,168}, /* Quantum.NanoCinematicSystem$$StopCleanly */
    {0x5be3bea,0x5be3bf3,0x5be3d7e,0,9,0x28,4,168}, /* Quantum.NanoCinematicSystem$$StopCleanly */
    {0x5be3c36,0x5be3c3f,0x5be3d24,0,2,0x28,4,168}, /* Quantum.NanoCinematicSystem$$StopCleanly */
    {0x5be431b,0x5be4320,0x5be4380,0,2,0x28,4,168}, /* Quantum.NanoCinematicSystem$$BeginSyncing */
    {0x5be4634,0x5be4639,0x5be4681,0,2,0x28,4,168}, /* Quantum.NanoCinematicSystem$$IsExecutingCinematic */
    {0x5be497f,0x5be4988,0x5be4f41,0,1,0x28,4,168}, /* Quantum.NanoCinematicSystem$$ClientNanoCinematicStatusUpdated */
    {0x5be49c0,0x5be49ca,0x5be4ee7,9,10,0x28,4,168}, /* Quantum.NanoCinematicSystem$$ClientNanoCinematicStatusUpdated */
    {0x5be4a24,0x5be4a2e,0x5be4e8d,9,10,0x28,4,168}, /* Quantum.NanoCinematicSystem$$ClientNanoCinematicStatusUpdated */
    {0x5be4a88,0x5be4a92,0x5be4e33,9,10,0x28,4,168}, /* Quantum.NanoCinematicSystem$$ClientNanoCinematicStatusUpdated */
    {0x5be4aec,0x5be4af6,0x5be4dd9,9,10,0x28,4,168}, /* Quantum.NanoCinematicSystem$$ClientNanoCinematicStatusUpdated */
    {0x5be4b4f,0x5be4b59,0x5be4d7f,9,10,0x28,4,168}, /* Quantum.NanoCinematicSystem$$ClientNanoCinematicStatusUpdated */
    {0x5be4c14,0x5be4c1e,0x5be4d19,8,9,0x28,4,168}, /* Quantum.NanoCinematicSystem$$ClientNanoCinematicStatusUpdated */
    {0x5be5240,0x5be5245,0x5be52b2,0,2,0x28,4,168}, /* Quantum.NanoCinematicSystem$$OnEntityDestroy */
    {0x603fb93,0x603fb9d,0x603fd65,12,2,0x23c,4,1320}, /* Quantum.CommandProcessingSystem$$ExecuteTeleportPlayerToCinematicCommand */
    {0x896b172,0x896b17b,0x896b36b,0,2,0x28,4,168}, /* Moon.Cinematics.CinematicsPreparation$$GetCinematicExecutionState */
    {0x896b1e4,0x896b1ed,0x896b311,0,9,0x28,4,168}, /* Moon.Cinematics.CinematicsPreparation$$GetCinematicExecutionState */
    {0x896b236,0x896b23b,0x896b2b7,0,2,0x28,4,168}, /* Moon.Cinematics.CinematicsPreparation$$GetCinematicExecutionState */
    {0x8d1d88c,0x8d1d895,0x8d1dcb5,0,1,0x28,4,168}, /* Moon.Forsaken.NanoCinematicView$$OnUpdate */
    {0x8d1ea76,0x8d1ea7f,0x8d1ed54,6,2,0x40,8,168}, /* Moon.Forsaken.NanoCinematicView$$ActuallyPlayTimeline */
    {0x8d205c1,0x8d205ca,0x8d20db3,0,2,0x28,4,168}, /* Moon.Forsaken.NanoCinematicView$$DoHacks */
};
static BOOL FiveOverlayInstall(void) {
    g_five_frame_tls = TlsAlloc();if (g_five_frame_tls == TLS_OUT_OF_INDEXES) return FALSE;
    g_five_class_methods = (FiveClassMethods)GetProcAddress((HMODULE)g_image, "il2cpp_class_get_methods");
    g_five_method_name = (FiveMethodName)GetProcAddress((HMODULE)g_image, "il2cpp_method_get_name");
    g_five_parameter_count = (FiveParameterCount)GetProcAddress((HMODULE)g_image, "il2cpp_method_get_param_count");
    g_five_parameter = (FiveParameter)GetProcAddress((HMODULE)g_image, "il2cpp_method_get_param");
    g_five_type_name = (FiveTypeName)GetProcAddress((HMODULE)g_image, "il2cpp_type_get_name");
    g_five_free = (FiveFree)GetProcAddress((HMODULE)g_image, "il2cpp_free");
    if (!g_five_class_methods || !g_five_method_name || !g_five_parameter_count || !g_five_parameter || !g_five_type_name || !g_five_free) return FALSE;
    if (!AddHook(0x5bf1830, FiveSimulateHook, (void **)&g_five_simulate) ||
        !AddHook(0x8928250, FiveViewFrameHook, (void **)&g_five_view_frame) ||
        !AddHook(0x89281d0, FiveVerifiedFrameHook, (void **)&g_five_verified_frame) ||
        !AddHook(0x8d35a80, FiveEntityTickHook, (void **)&g_five_entity_tick)) return FALSE;
    for (int i = 0; i < sizeof(g_five_mid_sites)/sizeof(g_five_mid_sites[0]); ++i) {
        if (!FiveInstallMid(&g_five_mid_sites[i])) {Log("five-player-mid-hook-rejected", g_five_mid_sites[i].rva);return FALSE;}
    }
    for (int i = 0; i < sizeof(g_five_raw_sites)/sizeof(g_five_raw_sites[0]); ++i) {
        if (!FiveInstallRaw(&g_five_raw_sites[i])) {Log("five-player-clock-hook-rejected", g_five_raw_sites[i].rva);return FALSE;}
    }
    return TRUE;
}

__declspec(dllexport) DWORD WINAPI WickedFivePlayerSelfTest(void *unused) {
    (void)unused;
    if (g_initialized) return 0;
    BYTE *saved_image = g_image;NativeConfig *saved_config = g_config;
    NativeConfig config;ZeroMemory(&config, sizeof(config));config.image_size = 0x10000;
    BYTE frame[0x2f0], session_config[0x60], buffer[0x48], block[16], component[168];
    ZeroMemory(frame, sizeof(frame));ZeroMemory(session_config, sizeof(session_config));
    ZeroMemory(buffer, sizeof(buffer));ZeroMemory(block, sizeof(block));ZeroMemory(component, sizeof(component));
    BYTE *globals = (BYTE *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, FIVE_EXTENDED_SIZE);
    BYTE *code = (BYTE *)VirtualAlloc(NULL, 0x10000, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!globals || !code) return 0;
    uint64_t entity = 0x100000001ull;
    *(void **)(frame + 0x2c8) = globals;*(void **)(frame + 0x2e0) = session_config;
    *(int *)(session_config + 0x10) = 5;*(void **)(frame + 0xd0) = buffer;*(int *)(frame + 0xd8) = 1;
    *(int *)(buffer + 0xc) = 168;*(int *)(buffer + 0x14) = 42;*(void **)(buffer + 0x18) = block;
    *(int *)(buffer + 0x24) = 1;*(int *)(buffer + 0x28) = 1;*(void **)block = &entity;*(void **)(block + 8) = component;
    *(uint64_t *)(globals + 0x6a0) = 0xcafebabedeadbeefull;
    g_image = code;g_config = &config;g_five_frame_tls = TlsAlloc();TlsSetValue(g_five_frame_tls, frame);
    DWORD bits = 0;
    BYTE *fifth = FiveComponentField(frame, component + 0x28, 0x28, 4, 168);
    if (fifth) {*(int *)fifth = 55;bits |= 1;}
    int values[] = {11,22,33,44};memcpy(component + 0x28, values, sizeof(values));
    const BYTE body[] = {0x48,0x83,0xec,0x28,0x8b,0xc1,0x83,0xf8,4,0x73,9,
                        0x8b,0x04,0x82,0x48,0x83,0xc4,0x28,0xc3,0x90,
                        0xb8,0xff,0xff,0xff,0xff,0x48,0x83,0xc4,0x28,0xc3};
    memcpy(code, body, sizeof(body));FiveMidSite site = {6,11,20,0,2,0x28,4,168};
    const BYTE extended_body[] = {0x48,0x83,0xec,0x28,0x41,0x89,0xc9,0x49,0x89,0xd2,
        0x41,0x83,0xf9,4,0x73,10,0x43,0x8b,0x04,0x8a,0x48,0x83,0xc4,0x28,0xc3,0x90,
        0xb8,0xff,0xff,0xff,0xff,0x48,0x83,0xc4,0x28,0xc3};
    memcpy(code + 0x100, extended_body, sizeof(extended_body));
    FiveMidSite extended_site = {0x10a,0x110,0x11a,9,10,0x28,4,168};
    if (FiveInstallMid(&site) && FiveInstallMid(&extended_site)) {
        typedef int (__cdecl *TestFn)(uint32_t, void *);TestFn test = (TestFn)code, extended = (TestFn)(code + 0x100);
        if (test(0, component + 0x28) == 11 && test(3, component + 0x28) == 44 && extended(3, component + 0x28) == 44) bits |= 2;
        if (test(4, component + 0x28) == 55 && extended(4, component + 0x28) == 55) bits |= 4;
        if (test(5, component + 0x28) == -1 && test(0xffffffffu, component + 0x28) == -1 && extended(5, component + 0x28) == -1) bits |= 8;
    }
    BOOL raw_passed = FALSE;
    const BYTE raw_body[] = {0x48,0x83,0xec,0x28,0x44,0x89,0x44,0x8a,0x04,0x44,0x89,0xc0,0x48,0x83,0xc4,0x28,0xc3};
    memcpy(code + 0x200, raw_body, sizeof(raw_body));
    *(uint32_t *)(component + 0x14) = 0x76543210u;
    FiveRawSite raw_site = {0x204,5,1,2,4,8,0,0,NULL,{0x44,0x89,0x44,0x8a,0x04}};
    if (MH_Initialize() == MH_OK && FiveInstallRaw(&raw_site) && MH_EnableHook(code + 0x204) == MH_OK) {
        typedef int (__cdecl *RawTestFn)(uint32_t, void *, uint32_t);RawTestFn raw_test = (RawTestFn)(code + 0x200);
        if (raw_test(3, component, 777) == 777 && raw_test(4, component, 888) == 888) {
            BYTE *clock = FiveComponentField(frame, component + 4, 4, 4, 168);
            raw_passed = clock && *(uint32_t *)clock == 888 && *(uint32_t *)(component + 0x10) == 777 && *(uint32_t *)(component + 0x14) == 0x76543210u;
        }
    }
    MH_DisableHook(MH_ALL_HOOKS);MH_Uninitialize();
    if (raw_passed && *(uint64_t *)(globals + 0x6a0) == 0xcafebabedeadbeefull && !memcmp(component + 0x28, values, sizeof(values))) bits |= 16;
    TlsFree(g_five_frame_tls);g_five_frame_tls = TLS_OUT_OF_INDEXES;
    VirtualFree(code, 0, MEM_RELEASE);HeapFree(GetProcessHeap(), 0, globals);
    if (g_five_stub_pool) VirtualFree(g_five_stub_pool, 0, MEM_RELEASE);
    g_five_stub_pool = NULL;g_five_stub_used = 0;g_image = saved_image;g_config = saved_config;
    return bits;
}
