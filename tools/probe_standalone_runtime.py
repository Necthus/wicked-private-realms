"""Development probe: load the verified IL2CPP image without the Unity player.

Native initialization is isolated in a disposable child with a hard timeout.
This is a feasibility diagnostic, not a dedicated game server.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def emit(event, **values):
    print(json.dumps(dict(event=event, **values)), flush=True)


def child(game: Path, skip_imgui=False, load_assets=False, runner_probe=False,
          runtime_config_file=None, session_config_file=None):
    # Never load the native image into the supervising process.
    os.chdir(game)
    dll_directory = os.add_dll_directory(str(game))
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel.GetModuleHandleW.restype = ctypes.c_void_p
    kernel.SetErrorMode(0x0001 | 0x0002 | 0x8000)
    # GameAssembly imports baselib.dll; load it explicitly as Unity's player normally does.
    ctypes.WinDLL(str(game / 'baselib.dll'))
    image = ctypes.WinDLL(str(game / 'GameAssembly.dll'))
    emit('native-image-loaded', unity_player_loaded=bool(kernel.GetModuleHandleW('UnityPlayer.dll')))

    def api(name, restype, *argtypes):
        fn = getattr(image, name)
        fn.restype, fn.argtypes = restype, argtypes
        return fn

    pointer, string = ctypes.c_void_p, ctypes.c_char_p
    data = game / 'NoRestForTheWicked_Data'
    argv_values = [b'AuthorityStandaloneProbe', b'-batchmode', b'-nographics']
    argv_array = (string * len(argv_values))(*argv_values)
    emit('setting-commandline')
    api('il2cpp_set_commandline_arguments', None, ctypes.c_int, ctypes.POINTER(string), string)(
        len(argv_values), argv_array, os.fsencode(game))
    emit('commandline-set')
    api('il2cpp_set_data_dir', None, string)(os.fsencode(data / 'il2cpp_data'))
    api('il2cpp_set_config_dir', None, string)(os.fsencode(data / 'il2cpp_data' / 'etc'))
    if skip_imgui:
        # Eager GUI function-pointer initialization is unrelated to simulation.
        # No-op only this exact-build cctor, in this disposable process's memory.
        address = image._handle + 0x7E51630
        previous = ctypes.c_ulong()
        protect = kernel.VirtualProtect
        protect.argtypes = [pointer, ctypes.c_size_t, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
        protect.restype = ctypes.c_int
        if not protect(address, 1, 0x40, ctypes.byref(previous)):
            raise ctypes.WinError(ctypes.get_last_error())
        ctypes.memmove(address, b'\xc3', 1)
        protect(address, 1, previous.value, ctypes.byref(previous))
        emit('imgui-eager-init-skipped')
    emit('initializing-il2cpp')
    initialized = api('il2cpp_init', ctypes.c_int, string)(b'AuthorityStandaloneProbe')
    emit('il2cpp-initialized', result=initialized)
    if not initialized:
        return 1
    domain = api('il2cpp_domain_get', pointer)()
    api('il2cpp_thread_attach', pointer, pointer)(domain)
    count = ctypes.c_size_t()
    assemblies = api('il2cpp_domain_get_assemblies', ctypes.POINTER(pointer), pointer, ctypes.POINTER(ctypes.c_size_t))(domain, ctypes.byref(count))
    get_image = api('il2cpp_assembly_get_image', pointer, pointer)
    get_class = api('il2cpp_class_from_name', pointer, pointer, string, string)
    get_method = api('il2cpp_class_get_method_from_name', pointer, pointer, string, ctypes.c_int)
    invoke = api('il2cpp_runtime_invoke', pointer, pointer, pointer, ctypes.POINTER(pointer), ctypes.POINTER(pointer))
    format_exception = api('il2cpp_format_exception', None, pointer, pointer, ctypes.c_int)
    emit('assemblies-available', count=count.value)

    def find_class(namespace, name):
        if '/' in name:
            outer, nested_name = name.split('/', 1)
            parent = find_class(namespace, outer)
            iterator = pointer()
            nested_types = api('il2cpp_class_get_nested_types', pointer, pointer, ctypes.POINTER(pointer))
            class_name = api('il2cpp_class_get_name', string, pointer)
            while nested := nested_types(parent, ctypes.byref(iterator)):
                if class_name(nested).decode() == nested_name:
                    return nested
            raise ValueError(f'Missing nested class: {namespace}.{name}')
        for i in range(count.value):
            klass = get_class(get_image(assemblies[i]), namespace.encode(), name.encode())
            if klass:
                return klass
        raise ValueError(f'Missing class: {namespace}.{name}')

    roots = []
    new_handle = api('il2cpp_gchandle_new', ctypes.c_uint32, pointer, ctypes.c_bool)

    def pin(obj):
        if obj:
            roots.append(new_handle(obj, False))
        return obj

    def call(namespace, name, method, self=None, args=()):
        klass = find_class(namespace, name)
        entry = get_method(klass, method.encode(), len(args))
        if not entry:
            raise ValueError(f'Missing method: {name}.{method}')
        error = pointer()
        parameters = (pointer * len(args))(*args) if args else None
        result = invoke(entry, self, parameters, ctypes.byref(error))
        if error.value:
            message = ctypes.create_string_buffer(8192)
            format_exception(error, message, len(message))
            raise RuntimeError(message.value.decode(errors='replace'))
        return pin(result)

    def new(namespace, name):
        obj = pin(api('il2cpp_object_new', pointer, pointer)(find_class(namespace, name)))
        call(namespace, name, '.ctor', obj)
        return obj

    def text(value):
        return pin(api('il2cpp_string_new', pointer, string)(os.fsencode(value)))

    def byte_array(value):
        array = pin(api('il2cpp_array_new', pointer, pointer, ctypes.c_size_t)(find_class('System', 'Byte'), len(value)))
        ctypes.memmove(array + 0x20, value, len(value))
        return array

    def scalar(value, ctype):
        holder = ctype(value)
        return holder, ctypes.addressof(holder)

    emit('initializing-dotnet-factory')
    call('Quantum', 'DotNetRunnerFactory', 'Init')
    emit('dotnet-factory-initialized')
    for name in ('CreateNativeAllocator', 'CreateNativeUtils', 'CreatePlatformInfo'):
        emit('factory-product', name=name, available=bool(call('Quantum', 'DotNetRunnerFactory', name)))
    if load_assets:
        emit('loading-simulation-assets')
        emit('loading-game-database-directly')
        assets = call('Quantum', 'Serialization', 'LoadAssetDatabaseFromFile', None,
                      (text(str(data / 'StreamingAssets' / 'quantumDatabase.bin')), text('standalone-probe')))
        emit('game-database-deserialized', count=ctypes.c_size_t.from_address(assets + 0x18).value if assets else None)
        simulation_config_class = find_class('Quantum', 'SimulationConfig')
        for index in range(ctypes.c_size_t.from_address(assets + 0x18).value):
            asset = pointer.from_address(assets + 0x20 + index * 8).value
            if asset and pointer.from_address(asset).value == simulation_config_class:
                emit('simulation-heap-config', pages=ctypes.c_int.from_address(asset + 0x184).value,
                     shift=ctypes.c_int.from_address(asset + 0x180).value,
                     extra=ctypes.c_int.from_address(asset + 0x188).value)
        serializer = new('Quantum', 'Serialization/AssetSerializer')
        context = new('Quantum', 'DotNetSessionContext')
        init_arguments = (pointer * 4)(text(str(data / 'StreamingAssets' / 'quantumLut')),
                                       text(str(data / 'StreamingAssets' / 'quantumDatabase.bin')),
                                       None, serializer)
        started = time.monotonic()
        call('Quantum', 'DotNetSessionContext', 'Init', context, (ctypes.addressof(init_arguments),))
        manager = call('Quantum', 'DotNetSessionContext', 'get_ResourceManager', context)
        dictionary = pointer.from_address(manager + 0x20).value if manager else None
        asset_count = ctypes.c_int.from_address(dictionary + 0x20).value if dictionary else 0
        shared_manager = call('Quantum', 'DotNetSessionContext', 'get_SharedResourceManager', context)
        shared_dictionary = pointer.from_address(shared_manager + 0x20).value if shared_manager else None
        shared_count = ctypes.c_int.from_address(shared_dictionary + 0x20).value if shared_dictionary else 0
        emit('simulation-assets-loaded', resource_manager=bool(manager), asset_count=asset_count,
             shared_asset_count=shared_count,
             seconds=round(time.monotonic() - started, 3))
        if runner_probe:
            if not runtime_config_file or not session_config_file:
                raise ValueError('--runner-probe requires runtime and session config files')
            runtime_bytes = Path(runtime_config_file).read_bytes()
            if runtime_bytes.startswith(b'\x1f\x8b'):
                import gzip
                runtime_bytes = gzip.decompress(runtime_bytes)
            session_bytes = Path(session_config_file).read_bytes()
            session_config = call('Photon.Deterministic', 'DeterministicSessionConfig', 'FromByteArray',
                                  None, [byte_array(session_bytes)])
            allocator = call('Quantum', 'DotNetRunnerFactory', 'CreateNativeAllocator')
            emit('standalone-allocator-created', available=bool(allocator))
            manager = api('il2cpp_object_new', pointer, pointer)(find_class('Quantum', 'ResourceManagerStatic'))
            pin(manager)
            allow_disposals, allow_address = scalar(True, ctypes.c_bool)
            call('Quantum', 'ResourceManagerStatic', '.ctor', manager,
                 [assets, allocator, allow_address])
            emit('standalone-resource-manager-created', available=bool(manager))
            # The generic factory currently produces QuantumGame, while the retail
            # session requires MoonQuantumGame and game-specific context. Keep the
            # exception as diagnostic evidence; do not replace its native factory
            # with a guessed ABI or write partially initialized managed objects.
            stream = pin(api('il2cpp_object_new', pointer, pointer)(find_class('System.IO', 'MemoryStream')))
            zero_index, zero_address = scalar(0, ctypes.c_int)
            length, length_address = scalar(len(runtime_bytes), ctypes.c_int)
            read_only, read_only_address = scalar(False, ctypes.c_bool)
            visible, visible_address = scalar(True, ctypes.c_bool)
            call('System.IO', 'MemoryStream', '.ctor', stream,
                 [byte_array(runtime_bytes), zero_address, length_address, read_only_address, visible_address])
            runtime_config = call('Quantum', 'Serialization/AssetSerializer', 'DeserializeConfig', serializer, [stream])
            arguments = call('Quantum', 'SessionRunner/Arguments', 'CreateDefault')
            raw_arguments = api('il2cpp_object_unbox', pointer, pointer)(arguments)
            pointer.from_address(raw_arguments + 0x00).value = text('StandaloneProbe')
            pointer.from_address(raw_arguments + 0x08).value = runtime_config
            pointer.from_address(raw_arguments + 0x10).value = session_config
            ctypes.c_int.from_address(raw_arguments + 0x20).value = 1
            ctypes.c_int.from_address(raw_arguments + 0x38).value = 4
            pointer.from_address(raw_arguments + 0x58).value = new('Quantum', 'DotNetRunnerFactory')
            pointer.from_address(raw_arguments + 0x70).value = manager
            pointer.from_address(raw_arguments + 0x78).value = serializer
            pointer.from_address(raw_arguments + 0x80).value = new('Quantum', 'CallbackDispatcher')
            pointer.from_address(raw_arguments + 0x88).value = new('Quantum', 'EventDispatcher')
            emit('starting-standalone-session')
            runner = call('Quantum', 'SessionRunner', 'Start', None, [raw_arguments])
            emit('standalone-runner-created', available=bool(runner))
            session = call('Quantum', 'SessionRunner', 'get_Session', runner)
            if not session:
                raise RuntimeError('Standalone runner returned no deterministic session')
            service_time = ctypes.create_string_buffer(16)
            ctypes.c_bool.from_buffer(service_time, 0).value = True
            ctypes.c_double.from_buffer(service_time, 8).value = 1.0 / 60.0
            ticks = []
            for _ in range(120):
                call('Quantum', 'SessionRunner', 'Service', runner, [ctypes.addressof(service_time)])
                frame = call('Photon.Deterministic', 'DeterministicSession', 'get_FrameVerified', session)
                ticks.append(ctypes.c_int.from_address(frame + 0x28).value if frame else -1)
            emit('standalone-simulation-advanced', first_tick=ticks[0], last_tick=ticks[-1],
                 real_simulation_verified=bool(ticks[-1] > ticks[0] >= 0))
    emit('standalone-runtime-ready', unity_player_loaded=bool(kernel.GetModuleHandleW('UnityPlayer.dll')),
         real_simulation_verified=False)
    # Process exit is safer than attempting Unity-owned teardown from this probe.
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-directory', type=Path)
    parser.add_argument('--timeout', type=int, default=45)
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--skip-imgui-eager-init', action='store_true', help='diagnostic: skip the exact-build GUI cctor in child memory')
    parser.add_argument('--load-assets', action='store_true', help='also try loading the real simulation database and fixed-point tables')
    parser.add_argument('--runner-probe', action='store_true', help='diagnostic only: try the generic SessionRunner (game-specific initialization is unresolved)')
    parser.add_argument('--runtime-config-file', type=Path)
    parser.add_argument('--session-config-file', type=Path)
    parser.add_argument('--trace-native', action='store_true', help='use optional Frida to record managed exception types and native stacks')
    args = parser.parse_args(argv)
    if args.runtime_config_file:
        args.runtime_config_file = args.runtime_config_file.resolve()
    if args.session_config_file:
        args.session_config_file = args.session_config_file.resolve()
    if not 5 <= args.timeout <= 120:
        parser.error('--timeout must be between 5 and 120')
    if args.runner_probe and not (args.load_assets and args.runtime_config_file and args.session_config_file):
        parser.error('--runner-probe requires --load-assets and both config files')
    if sys.platform != 'win32' or ctypes.sizeof(ctypes.c_void_p) != 8:
        parser.error('this IL2CPP image probe requires Windows x64')
    game = args.game_directory
    if game is None:
        game = Path(json.loads((ROOT / 'workspace.local.json').read_text(encoding='utf-8-sig'))['game_directory'])
    game = game.resolve()
    from tools.probe_authority_runtime import verify_installation
    fingerprints = verify_installation(game)
    if args.child:
        try:
            return child(game, args.skip_imgui_eager_init, args.load_assets, args.runner_probe,
                         args.runtime_config_file, args.session_config_file)
        except Exception as error:
            emit('standalone-error', error=str(error))
            return 1
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lab = ROOT / 'dev-artifacts/authority-probe' / f'standalone-{stamp}-{secrets.token_hex(3)}'
    lab.mkdir(parents=True)
    try:
        command = [sys._base_executable, '-u', str(Path(__file__).resolve()), '--child', '--game-directory', str(game)]
        if args.skip_imgui_eager_init:
            command.append('--skip-imgui-eager-init')
        if args.load_assets:
            command.append('--load-assets')
        if args.runner_probe:
            command.extend(['--runner-probe', '--runtime-config-file', str(args.runtime_config_file),
                            '--session-config-file', str(args.session_config_file)])
        if args.trace_native:
            import frida
            device = frida.get_local_device()
            stdout_parts, trace_events = [], []
            pid = device.spawn(command, cwd=str(ROOT), stdio='pipe')
            session = device.attach(pid)
            done = threading.Event()
            session.on('detached', lambda *_: done.set())
            def output(process_id, fd, data):
                if process_id == pid:
                    stdout_parts.append(data)
            device.on('output', output)
            script = session.create_script((ROOT / 'tools/authority_probe/standalone_trace.js').read_text(encoding='utf-8'))
            script.on('message', lambda message, _data: trace_events.append(message))
            try:
                script.load()
                device.resume(pid)
                timed_out = not done.wait(args.timeout)
            finally:
                try:
                    device.kill(pid)
                except frida.ProcessNotFoundError:
                    pass
                try:
                    session.detach()
                except frida.InvalidOperationError:
                    pass
                device.off('output', output)
            stdout, stderr, code = b''.join(stdout_parts), b'', None
            (lab / 'native-exceptions.json').write_text(json.dumps(trace_events, indent=2), encoding='utf-8')
        else:
            result = subprocess.run(command, capture_output=True, timeout=args.timeout)
            stdout, stderr, code, timed_out = result.stdout, result.stderr, result.returncode, False
    except subprocess.TimeoutExpired as error:
        stdout, stderr, code, timed_out = error.stdout or b'', error.stderr or b'', None, True
    (lab / 'stdout.log').write_bytes(stdout)
    (lab / 'stderr.log').write_bytes(stderr)
    events = []
    for line in stdout.decode(errors='replace').splitlines():
        try:
            event = json.loads(line)
            if isinstance(event, dict):
                events.append(event)
        except ValueError:
            pass
    database = next((e for e in events if e.get('event') == 'game-database-deserialized'), {})
    context = next((e for e in events if e.get('event') == 'simulation-assets-loaded'), {})
    report = dict(exit_code=code, timed_out=timed_out, events=events,
                  game_fingerprints=fingerprints, real_simulation_verified=False,
                  dedicated_server_ready=False,
                  deserialized_asset_count=database.get('count', 0),
                  context_asset_count=context.get('asset_count', 0))
    (lab / 'result.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    emit('standalone-probe-result', output=str(lab), **report)
    return 0 if code == 0 and not timed_out else 1


if __name__ == '__main__':
    raise SystemExit(main())
