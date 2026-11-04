#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <windows.h>
#include <winternl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>
#include "MinHook.h"

#pragma pack(push, 1)
typedef struct NativeConfig {
    char magic[8];
    uint32_t version, size;
    volatile LONG status, error;
    uint32_t image_size, image_timestamp;
    char steam_id[32], endpoint[128], token[4096];
    wchar_t log_path[1024];
    uint32_t flags;
} NativeConfig;
#pragma pack(pop)
C_ASSERT(sizeof(NativeConfig) == 6340);

static NativeConfig *g_config;
static HANDLE g_mapping;
static BYTE *g_image;
static volatile LONG g_initialized, g_installing;
static SRWLOCK g_log_lock = SRWLOCK_INIT, g_string_lock = SRWLOCK_INIT;
static DWORD g_peer_tls = TLS_OUT_OF_INDEXES;
typedef void *(__cdecl *StringNew)(const char *);
typedef uint32_t (__cdecl *GcNew)(void *, uint8_t);
typedef void (__cdecl *GcFree)(uint32_t);
static StringNew g_string_new;
static GcNew g_gc_new;
static GcFree g_gc_free;
static void *g_strings[7];
static uint32_t g_string_handles[7];

static void Log(const char *event, LONG value) {
    if (!g_config || !g_config->log_path[0]) return;
    SYSTEMTIME now; char line[512]; DWORD written;
    GetSystemTime(&now);
    int length = _snprintf_s(line, sizeof(line), _TRUNCATE,
        "{\"time\":\"%04u-%02u-%02uT%02u:%02u:%02u.%03uZ\",\"event\":\"%s\",\"value\":%ld}\n",
        now.wYear, now.wMonth, now.wDay, now.wHour, now.wMinute, now.wSecond,
        now.wMilliseconds, event, value);
    if (length <= 0) return;
    AcquireSRWLockExclusive(&g_log_lock);
    HANDLE file = CreateFileW(g_config->log_path, FILE_APPEND_DATA,
        FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file != INVALID_HANDLE_VALUE) { WriteFile(file, line, (DWORD)length, &written, NULL); CloseHandle(file); }
    ReleaseSRWLockExclusive(&g_log_lock);
}
static void Fatal(LONG code, const char *event) {
    if (g_config) { InterlockedExchange(&g_config->error, code); InterlockedExchange(&g_config->status, -1); }
    Log(event, code);
    TerminateProcess(GetCurrentProcess(), 0xe0010000u | ((DWORD)code & 0xffffu));
}
static int ManagedLength(void *value) { return value ? *(int *)((BYTE *)value + 16) : -1; }
static const wchar_t *ManagedChars(void *value) { return (const wchar_t *)((BYTE *)value + 20); }
static BOOL ManagedEquals(void *value, const wchar_t *expected) {
    int length = ManagedLength(value);
    return length >= 0 && (size_t)length == wcslen(expected) &&
        wmemcmp(ManagedChars(value), expected, (size_t)length) == 0;
}
static void *ManagedCached(int index) {
    static const char *suffixes[4] = { "/auth", "/rest", "/gatekeeper", "/queue" };
    void *result;
    AcquireSRWLockExclusive(&g_string_lock);
    if (!g_strings[index]) {
        char text[4352];
        if (index < 4) _snprintf_s(text, sizeof(text), _TRUNCATE, "%s%s", g_config->endpoint, suffixes[index]);
        else if (index == 4) strcpy_s(text, sizeof(text), g_config->token);
        else strcpy_s(text, sizeof(text), "127.0.0.1");
        result = g_string_new(text);
        if (!result) { ReleaseSRWLockExclusive(&g_string_lock); Fatal(101, "managed-string-failed"); return NULL; }
        g_string_handles[index] = g_gc_new(result, 0);
        g_strings[index] = result;
    }
    result = g_strings[index];
    ReleaseSRWLockExclusive(&g_string_lock);
    return result;
}
static void PatchSdk(void *sdk) {
    static const size_t offsets[4] = { 0x30, 0x40, 0x48, 0x50 };
    if (!sdk) return;
    for (int i = 0; i < 4; ++i) *(void **)((BYTE *)sdk + offsets[i]) = ManagedCached(i);
}

typedef void (__cdecl *MoveNext)(void *, void *);
static MoveNext g_login, g_request;
static MoveNext g_tick, g_game_started;
typedef void (__cdecl *PlayerAdded)(void *, void *, int, int, void *);
static PlayerAdded g_added;
static void __cdecl TickHook(void *self, void *method) {
    static LONG last_state = -100;
    int state = *(int *)((BYTE *)self + 0x10);
    if (state != last_state) { InterlockedExchange(&last_state, state); Log("native-session-state", state); }
    g_tick(self, method);
}
static void __cdecl StartedHook(void *self, void *method) { Log("native-game-started", 1); g_game_started(self, method); }
static void __cdecl AddedHook(void *self, void *frame, int slot, int player, void *method) {
    Log("native-player-add-confirmed", frame ? *(int *)((BYTE *)frame + 0x28) : -1);
    g_added(self, frame, slot, player, method);
}
static void __cdecl LoginHook(void *self, void *method) {
    void *backend = *(void **)((BYTE *)self + 0x20);
    if (*(int *)self == -1) { *(void **)((BYTE *)self + 0x30) = ManagedCached(4); Log("private-login-token-bound", 1); }
    if (backend) PatchSdk(*(void **)((BYTE *)backend + 0x10));
    g_login(self, method);
}
static void __cdecl RequestHook(void *self, void *method) {
    void *request = *(void **)((BYTE *)self + 0x28);
    if (request) PatchSdk(*(void **)((BYTE *)request + 0x10));
    g_request(self, method);
}

typedef void (__cdecl *OfflineInit)(void *, void *, int, void *, void *, void *);
static OfflineInit g_offline_init;
static void __cdecl OfflineHook(void *self, void *path, int build, void *account, void *override, void *method) {
    wchar_t expected[32];
    MultiByteToWideChar(CP_UTF8, 0, g_config->steam_id, -1, expected, 32);
    if (!ManagedEquals(account, expected)) { Fatal(102, "steam-identity-mismatch"); return; }
    Log("native-steam-save-path", build);
    g_offline_init(self, path, build, account, override, method);
}
typedef uint8_t (__cdecl *IsSolo)(void *, void *);
typedef void *(__cdecl *GetEntry)(void *, void *, void *);
static IsSolo g_is_solo;
static GetEntry g_get_entry;
static uint8_t __cdecl SoloHook(void *realm, void *method) {
    uint8_t solo = g_is_solo(realm, method);
    if (!solo) return 0;
    void *owner = *(void **)realm;
    if (owner) {
        BYTE *entry = (BYTE *)g_get_entry((BYTE *)owner + 0x118, (BYTE *)realm + 8, NULL);
        if (entry && *(int *)(entry + 0x10) == -1 && *(int *)(entry + 0x18) >= 0) return 0;
    }
    return solo;
}
typedef uint8_t (__cdecl *Connect)(void *, void *, void *);
static Connect g_connect;
static uint8_t __cdecl ConnectHook(void *self, void *settings, void *method) {
    BYTE *p = (BYTE *)settings;
    p[0x40] = 0; *(void **)(p + 0x58) = ManagedCached(5); *(uint16_t *)(p + 0x60) = 18769;
    p[0x70] = 1; p[0x71] = 0; *(int *)(p + 0x74) = 0;
    Log("private-photon-connect", 18769);
    return g_connect(self, settings, method);
}
typedef void (__cdecl *JoinSession)(void *, void *, void *, uint8_t, uint8_t, void *);
static JoinSession g_join;
static void __cdecl JoinHook(void *self, void *character, void *realm, uint8_t prologue, uint8_t respawn, void *method) {
    Log("session-join-request", prologue);
    g_join(self, character, realm, (g_config->flags & 1) ? 0 : prologue, respawn, method);
}
static MoveNext g_shared_ready;
static void __cdecl SharedReadyHook(void *self, void *method) {
    BYTE *frame = *(BYTE **)((BYTE *)self + 0x10);
    BYTE *flow = frame ? *(BYTE **)(frame + 0x4d8) : NULL;
    BYTE *request = flow ? *(BYTE **)(flow + 0x48) : NULL;
    BYTE *task = request ? *(BYTE **)(request + 0x18) : NULL;
    if (task && (*(uint32_t *)(task + 0x38) & 0x1600000u) == 0x1000000u && *(int *)(task + 0x50) == 1001) {
        Log("join-rejected-private-transport-unavailable", 1001);
        ((MoveNext)(g_image + 0x890f5e0))(self, NULL);
        ((MoveNext)(g_image + 0x890bcb0))(self, NULL);
        return;
    }
    g_shared_ready(self, method);
}

static BOOL EndsWith(const char *text, const char *suffix) {
    size_t a = strlen(text), b = strlen(suffix);
    return a >= b && _stricmp(text + a - b, suffix) == 0;
}
static void *RedirectUri(void *uri, uint32_t *handle) {
    *handle = 0;
    int length = ManagedLength(uri);
    if (length <= 0 || length > 4096) return uri;
    char original[16388], host[1024], rewritten[16640];
    int size = WideCharToMultiByte(CP_UTF8, 0, ManagedChars(uri), length, original, sizeof(original) - 1, NULL, NULL);
    if (size <= 0) return uri;
    original[size] = 0;
    char *start = NULL;
    if (_strnicmp(original, "https://", 8) == 0) start = original + 8;
    else if (_strnicmp(original, "http://", 7) == 0) start = original + 7;
    if (!start) return uri;
    char *path = strchr(start, '/');
    size_t host_length = path ? (size_t)(path - start) : strlen(start);
    if (host_length >= sizeof(host)) return uri;
    memcpy(host, start, host_length); host[host_length] = 0;
    if (!EndsWith(host, ".moonbackend.net") && !EndsWith(host, ".norestforthewickedbackend.net")) return uri;
    _strlwr_s(host, sizeof(host));
    _snprintf_s(rewritten, sizeof(rewritten), _TRUNCATE, "%s%s%s", g_config->endpoint,
        strstr(host, "gatekeeper") ? "/gatekeeper" : "", path ? path : "/");
    void *result = g_string_new(rewritten);
    if (!result) { Fatal(103, "uri-string-failed"); return uri; }
    *handle = g_gc_new(result, 0);
    Log("private-backend-uri-redirected", 1);
    return result;
}
typedef void (__cdecl *Uri1)(void *, void *, void *);
typedef void (__cdecl *Uri2)(void *, void *, int, void *);
typedef void (__cdecl *Uri3)(void *, void *, uint8_t, int, void *);
static Uri1 g_uri1; static Uri2 g_uri2; static Uri3 g_uri3;
static void __cdecl Uri1Hook(void *self, void *uri, void *method) { uint32_t h; uri = RedirectUri(uri, &h); g_uri1(self, uri, method); if (h) g_gc_free(h); }
static void __cdecl Uri2Hook(void *self, void *uri, int kind, void *method) { uint32_t h; uri = RedirectUri(uri, &h); g_uri2(self, uri, kind, method); if (h) g_gc_free(h); }
static void __cdecl Uri3Hook(void *self, void *uri, uint8_t escape, int kind, void *method) { uint32_t h; uri = RedirectUri(uri, &h); g_uri3(self, uri, escape, kind, method); if (h) g_gc_free(h); }

typedef uint8_t (__cdecl *PeerSend)(void *, void *);
typedef void (__cdecl *PeerPing)(void *, uint8_t, void *);
typedef int64_t (__cdecl *Elapsed)(void *, void *);
typedef int (WSAAPI *SocketSend)(SOCKET, const char *, int, int);
static PeerSend g_outgoing, g_acks; static PeerPing g_ping;
static Elapsed g_elapsed; static SocketSend g_send;
static BOOL IsPrivatePeer(void *peer) {
    __try { return peer && ManagedEquals(*(void **)((BYTE *)peer + 0x30), L"127.0.0.1:18769"); }
    __except (EXCEPTION_EXECUTE_HANDLER) { return FALSE; }
}
static void *EnterPeer(void *peer) {
    void *previous = TlsGetValue(g_peer_tls);
    TlsSetValue(g_peer_tls, IsPrivatePeer(peer) ? peer : NULL);
    return previous;
}
static uint8_t __cdecl OutgoingHook(void *self, void *method) { void *old = EnterPeer(self); uint8_t result = g_outgoing(self, method); TlsSetValue(g_peer_tls, old); return result; }
static uint8_t __cdecl AcksHook(void *self, void *method) { void *old = EnterPeer(self); uint8_t result = g_acks(self, method); TlsSetValue(g_peer_tls, old); return result; }
static void __cdecl PingHook(void *self, uint8_t immediate, void *method) { void *old = EnterPeer(self); g_ping(self, immediate, method); TlsSetValue(g_peer_tls, old); }
static int WSAAPI SendHook(SOCKET socket, const char *data, int length, int flags) {
    void *peer = TlsGetValue(g_peer_tls);
    if (peer && length == 5 && (uint8_t)data[0] == 240) {
        void *watch = *(void **)((BYTE *)peer + 0xc0);
        if (watch) {
            uint32_t stamp = (uint32_t)g_elapsed(watch, NULL);
            uint8_t fixed[5] = { 240, (uint8_t)(stamp >> 24), (uint8_t)(stamp >> 16), (uint8_t)(stamp >> 8), (uint8_t)stamp };
            return g_send(socket, (const char *)fixed, 5, flags);
        }
    }
    return g_send(socket, data, length, flags);
}

static BOOL AddHook(size_t rva, void *detour, void **original) {
    MH_STATUS status = MH_CreateHook(g_image + rva, detour, original);
    if (status != MH_OK) { Log("native-hook-create-failed", (LONG)status); return FALSE; }
    return TRUE;
}
static void InstallGameHooks(HMODULE module) {
    if (InterlockedCompareExchange(&g_installing, 1, 0) != 0) return;
    g_image = (BYTE *)module;
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)g_image;
    IMAGE_NT_HEADERS64 *nt = (IMAGE_NT_HEADERS64 *)(g_image + dos->e_lfanew);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE || nt->Signature != IMAGE_NT_SIGNATURE ||
        nt->FileHeader.Machine != IMAGE_FILE_MACHINE_AMD64 || nt->OptionalHeader.SizeOfImage != g_config->image_size ||
        nt->FileHeader.TimeDateStamp != g_config->image_timestamp) { Fatal(104, "game-image-layout-mismatch"); return; }
    g_string_new = (StringNew)GetProcAddress(module, "il2cpp_string_new");
    g_gc_new = (GcNew)GetProcAddress(module, "il2cpp_gchandle_new");
    g_gc_free = (GcFree)GetProcAddress(module, "il2cpp_gchandle_free");
    if (!g_string_new || !g_gc_new || !g_gc_free) { Fatal(105, "il2cpp-exports-missing"); return; }
    g_get_entry = (GetEntry)(g_image + 0x8aebf10); g_elapsed = (Elapsed)(g_image + 0x77e1850);
    if (!AddHook(0x58239e0, LoginHook, (void **)&g_login) ||
        !AddHook(0x5845e00, RequestHook, (void **)&g_request) ||
        !AddHook(0x8abc680, OfflineHook, (void **)&g_offline_init) ||
        !AddHook(0x8aee5a0, SoloHook, (void **)&g_is_solo) ||
        !AddHook(0x70496f0, ConnectHook, (void **)&g_connect) ||
        !AddHook(0x890d2c0, JoinHook, (void **)&g_join) ||
        !AddHook(0x890f060, SharedReadyHook, (void **)&g_shared_ready) ||
        !AddHook(0x770d620, Uri1Hook, (void **)&g_uri1) ||
        !AddHook(0x770d730, Uri2Hook, (void **)&g_uri2) ||
        !AddHook(0x771bc50, Uri3Hook, (void **)&g_uri3) ||
        !AddHook(0x70a2940, OutgoingHook, (void **)&g_outgoing) ||
        !AddHook(0x70a2e90, AcksHook, (void **)&g_acks) ||
        !AddHook(0x70a3340, PingHook, (void **)&g_ping) ||
        !AddHook(0x871c5d0, TickHook, (void **)&g_tick) ||
        !AddHook(0x8719090, StartedHook, (void **)&g_game_started) ||
        !AddHook(0x5bf6470, AddedHook, (void **)&g_added)) { Fatal(106, "game-hook-install-failed"); return; }
    void *send = (void *)GetProcAddress(GetModuleHandleW(L"ws2_32.dll"), "send");
    if (!send || MH_CreateHook(send, SendHook, (void **)&g_send) != MH_OK || MH_EnableHook(MH_ALL_HOOKS) != MH_OK) {
        Fatal(107, "native-network-hook-failed"); return;
    }
    InterlockedExchange(&g_config->status, 3);
    Log("native-game-hooks-installed", 17);
}
typedef NTSTATUS (NTAPI *LdrLoad)(PWSTR, PULONG, PUNICODE_STRING, PHANDLE);
static LdrLoad g_ldr_load;
static NTSTATUS NTAPI LdrHook(PWSTR path, PULONG flags, PUNICODE_STRING name, PHANDLE result) {
    NTSTATUS status = g_ldr_load(path, flags, name, result);
    if (status >= 0 && result && *result && name && name->Buffer) {
        size_t count = name->Length / sizeof(wchar_t);
        const wchar_t *base = name->Buffer;
        for (size_t i = 0; i < count; ++i) if (base[i] == L'\\' || base[i] == L'/') { base += i + 1; count -= i + 1; i = (size_t)-1; }
        if (count == 16 && _wcsnicmp(base, L"GameAssembly.dll", 16) == 0) InstallGameHooks((HMODULE)*result);
    }
    return status;
}

__declspec(dllexport) DWORD WINAPI WickedAdapterInitialize(void *unused) {
    (void)unused;
    if (InterlockedCompareExchange(&g_initialized, 1, 0) != 0) return 1;
    wchar_t name[256];
    if (!GetEnvironmentVariableW(L"WICKED_REALM_MAPPING", name, 256)) return 201;
    g_mapping = OpenFileMappingW(FILE_MAP_READ | FILE_MAP_WRITE, FALSE, name);
    if (!g_mapping) return 202;
    g_config = (NativeConfig *)MapViewOfFile(g_mapping, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, 8192);
    if (!g_config || memcmp(g_config->magic, "WRNAT01", 7) || g_config->version != 1 || g_config->size != sizeof(NativeConfig)) return 203;
    if (g_config->steam_id[31] || strlen(g_config->steam_id) != 17 || g_config->endpoint[127] ||
        strcmp(g_config->endpoint, "http://127.0.0.1:18770/game") || g_config->token[4095] || !g_config->token[0]) return 204;
    InterlockedExchange(&g_config->status, 1);
    g_peer_tls = TlsAlloc();
    if (g_peer_tls == TLS_OUT_OF_INDEXES) return 205;
    MH_STATUS mh = MH_Initialize();
    if (mh != MH_OK && mh != MH_ERROR_ALREADY_INITIALIZED) return 206;
    void *load = (void *)GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "LdrLoadDll");
    if (!load || MH_CreateHook(load, LdrHook, (void **)&g_ldr_load) != MH_OK || MH_EnableHook(load) != MH_OK) return 207;
    HMODULE image = GetModuleHandleW(L"GameAssembly.dll");
    if (image) InstallGameHooks(image);
    InterlockedCompareExchange(&g_config->status, 2, 1);
    Log("native-adapter-ready", 1);
    return 0;
}

static volatile int g_selftest_value = 7;
__declspec(noinline) static int SelfTestTarget(int value) { return value + g_selftest_value; }
typedef int (*SelfTestFn)(int);
static SelfTestFn g_selftest_original;
static int SelfTestDetour(int value) { return g_selftest_original(value) + 100; }
static void *FakeString(const char *text) {
    int n = MultiByteToWideChar(CP_UTF8, 0, text, -1, NULL, 0);
    BYTE *result = (BYTE *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, 20 + (size_t)n * 2);
    if (!result) return NULL;
    *(int *)(result + 16) = n - 1;
    MultiByteToWideChar(CP_UTF8, 0, text, -1, (wchar_t *)(result + 20), n);
    return result;
}
static uint32_t FakeGc(void *value, uint8_t pinned) { (void)value; (void)pinned; return 1; }
__declspec(dllexport) DWORD WINAPI WickedAdapterSelfTest(void *unused) {
    (void)unused;
    DWORD bits = 0;
    if (MH_Initialize() != MH_OK) return 0;
    volatile SelfTestFn target = SelfTestTarget;
    if (target(2) == 9 && MH_CreateHook(SelfTestTarget, SelfTestDetour, (void **)&g_selftest_original) == MH_OK &&
        MH_EnableHook(SelfTestTarget) == MH_OK && target(2) == 109) bits |= 1;
    MH_DisableHook(SelfTestTarget); MH_RemoveHook(SelfTestTarget); MH_Uninitialize();
    NativeConfig config; ZeroMemory(&config, sizeof(config));
    strcpy_s(config.endpoint, sizeof(config.endpoint), "http://127.0.0.1:18770/game"); strcpy_s(config.token, sizeof(config.token), "selftest-token");
    g_config = &config; g_string_new = FakeString; g_gc_new = FakeGc;
    BYTE sdk[0x60]; ZeroMemory(sdk, sizeof(sdk)); PatchSdk(sdk);
    if (ManagedEquals(*(void **)(sdk + 0x30), L"http://127.0.0.1:18770/game/auth") &&
        ManagedEquals(*(void **)(sdk + 0x48), L"http://127.0.0.1:18770/game/gatekeeper")) bits |= 2;
    BYTE peer[0xd0]; ZeroMemory(peer, sizeof(peer));
    *(void **)(peer + 0x30) = FakeString("127.0.0.1:18769");
    if (IsPrivatePeer(peer)) bits |= 4;
    *(void **)(peer + 0x30) = FakeString("official.invalid:5055");
    if (!IsPrivatePeer(peer)) bits |= 8;
    if (ManagedEquals(ManagedCached(4), L"selftest-token")) bits |= 16;
    g_config = NULL;
    return bits;
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved) {
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) DisableThreadLibraryCalls(instance);
    return TRUE;
}
