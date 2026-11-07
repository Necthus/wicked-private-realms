# 服务端权威模拟：本机可行性记录

日期：2026-11-07。分支：`codex/server-authoritative-state-sync`。

## 结论

可以先在同一台 Windows 机器上研究和测试私服、专用模拟进程及玩家客户端。已通过独立模拟、快照往返、连续状态复制，以及两个独立游戏进程之间的真实角色移动、输入上行和双向断流恢复探针。

单玩家跨进程探针已验证原生 47 字节输入和完整游戏帧。输入由客户端脚本生成，角色视图通过运行时绑定状态确认；Windows 画面捕获连续超时，尚未完成像素画面的视觉检查。第二玩家加入及输入/命令传输已通过回环开发探针；两台实体电脑的实际人工操作、战斗及交互结果、表现平滑和正式权威存档仍需验证。默认服务端仍运行原有输入同步模式。

普通游戏启动参数不能直接把零售客户端变成专用服务端。批处理方式可以运行无角色的底层模拟探针，但真实角色与场景初始化还依赖渲染链路。目前跨进程探针使用 640×360 窗口，需要图形设备和完整游戏安装。

### 低配独立服务器的约束与无图形复测

目标部署环境是 Windows 或 Linux 的 2 核 4 GB 服务器，双人测试使用两台实体电脑。这个目标要求继续验证独立、无图形的模拟运行时；多开一份完整零售游戏不能作为低配服务器方案。

2026-11-07 新增 `--nographics` 诊断入口并复测 v29466：Unity 日志确认使用 `Renderer: Null Device`，但游戏在约 2 秒后因 `System.InvalidOperationException: Cannot use SoundbanksInfoMediaResolver till it is initialized.` 退出，未创建可推进的真实角色模拟。日志另有视频和渲染 shader 不可用信息，不能据此认定绕过音频后就一定能运行。记录目录为 `dev-artifacts/authority-probe/probe-20261107T062741Z-864405/`。

已有带图形的三进程交互实验中，每个游戏进程约占 5–6 GB 工作集、9–10 GB 私有提交。这只能说明当前完整游戏运行路径不适合 4 GB 服务器，不能预测尚未实现的纯模拟运行时的资源需求。CPU 容量也未按双核目标做过验收。

`DotNetSessionRunner` 和 `SessionDefinition.IsHeadlessSession` 仍为研究入口。必须验证真实世界和角色推进、输入处理、资产依赖、快照恢复及资源占用，才能确认低配独立权威方案可行。用户已同意在 headless 难以实现时先采用房主模拟；当前按这一备选方案提供双机测试入口，独立服务器目标仍待后续研究。

### 2026-11-07 独立 IL2CPP 复测

新增 `tools/probe_standalone_runtime.py`，在隔离子进程中直接加载 `GameAssembly.dll`，不加载 `UnityPlayer.dll`。首次调用 `il2cpp_init` 会因为 `Moon.DearImGuiBindings.MoonImGuiFnPtrs::GetMoonImGuiFnPointers` 的静态构造依赖 Unity 注册的内部调用而抛出 `MissingMethodException`。在仅为诊断而跳过这个精确构建的 GUI 静态构造后，IL2CPP、`DotNetRunnerFactory`、定点数学表和 `quantumDatabase.bin` 均可初始化；数据库反序列化得到 30,406 个资源对象。

随后用真实会话配置和运行时配置尝试 `DotNetSessionRunner.Start`。当前纯 Quantum 参数只能构造普通 `QuantumGame`，而游戏运行器要求 `MoonQuantumGame` 和游戏专用的 `QuantumGameStartParameters`、回调及资产解析上下文，触发 `InvalidCastException: Unable to cast object of type 'QuantumGame' to type 'MoonQuantumGame'`，随后 `NullReferenceException`。因此当前版本已经证明“脱离 Unity 加载 IL2CPP 和资源”可行，但尚未证明“脱离零售客户端启动真实游戏模拟”可行；不能把这个诊断入口当作可部署 headless 服务端。

记录目录：`dev-artifacts/authority-probe/standalone-20261107T065926Z-2c1d78/`（资源加载）、`standalone-20261107T070425Z-b1e9bf/`（运行器异常堆栈）。

## 已完成检查

- EXE、`GameAssembly.dll` 和 `global-metadata.dat` 的 SHA-256 全部匹配 `research/client-fingerprint.json`。
- 游戏日志标识 Unity `6000.1.15f1m1`；metadata 格式为 31。
- 开发机：Windows 10 x64、Ryzen 9 7950X、32 GB 内存、RX 9070 XT。两个完整场景进程运行时，各自私有提交约 9–10 GB、工作集约 5–6 GB；启动和场景加载时的需求会变化。
- 用独立虚拟环境安装开发依赖，Python 包从清华镜像获取。没有使用远程机器。
- 构建当前原生宿主，22 项宿主自检通过；共享房间与独立服务端的 16 项测试通过。
- Python 私服绑定 `127.0.0.1`，网关与内部监听使用系统分配端口，使用独立测试数据库与合成账号。原生宿主 `--network-check` 登录成功，随后停止该私服。

## 已定位的候选入口

以下是本机 metadata 导出中的方法定义及其 RVA。序列化、反序列化和恢复路径已经运行探针验证；独立运行器候选仍待进一步评估。所有地址仅对应已核对的 v29466，不直接作为生产功能启用。

| 候选方法 | RVA | 需要验证的用途 |
| --- | --- | --- |
| `frameworks.session.LockstepAPI.ResyncToFrame(byte[], int)` | `0x8720970` | 在同步上下文中恢复指定帧 |
| `Quantum.Frame.Serialize(DeterministicFrameSerializeMode)` | `0x596A9F0` | 导出可恢复的完整模拟状态 |
| `Quantum.Frame.Deserialize(byte[])` | `0x596BCB0` | 恢复模拟状态 |
| `Quantum.SessionRunner.Start(SessionRunner.Arguments)` | `0x5EE7DC0` | 创建独立会话运行器 |
| `Quantum.DotNetSessionRunner.Start(DeterministicSessionRunnerStartArguments)` | `0x5EE7190` | 研究运行器及资源依赖 |
| `Photon.Deterministic.DeterministicSession.Resync(byte[], int)` | `0x70DE230` | Quantum 底层状态恢复 |
| `frameworks.session.SessionHandlerAPI.RequestHeadlessOnlineSession(...)` | `0x87265C0` | 研究游戏已有的无视图会话路径 |

还发现 `SessionDefinition.IsHeadlessSession` 和 `SessionDefinition.CommandToSendThenDestroy`。后者表明现有 headless 路径可能面向短时发送命令后销毁的任务，不能据此认定它支持持续权威模拟。

`DotNetSessionRunner` 在 IL2CPP 二进制中的存在，也不能证明仓库已经有可直接运行的独立 .NET 服务端程序集。metadata 导出提供类型、签名和地址，不恢复方法实现。

## 启动探针结果

使用开发专用 Frida 探针，加载归档重定向脚本，把游戏存档入口指向每次运行的独立目录；正式原生客户端仍使用原生适配器。探针未修改游戏 DLL。探针中的私服接口使用未监听的回环地址或独立的临时本机后端。

| 探针 | 实际观察 | 未证明的能力 |
| --- | --- | --- |
| `-batchmode -nographics` | 引擎及钩子加载；存档重定向命中；音频初始化抛出 `SoundbanksInfoMediaResolver` 尚未初始化异常并退出 | 独立世界模拟 |
| 上述参数，加临时跳过音频初始化 | 会话管理循环继续运行；随后出现 `ScreenFader` 空引用和 `Texture2DArray` 创建失败，进程退出 | 稳定无图形运行；管理循环计数不是模拟帧计数 |
| `-batchmode` 保留图形，加临时跳过音频初始化和独立后端 | 本机游戏 gatekeeper、认证及 queue 接口返回成功；仍出现 `ScreenFader` 空引用，进程退出 | 进入真实国度、生成权威快照和客户端校正 |

跳过音频仅用于识别下一项依赖，不是已完成的运行时适配。探针均已结束，没有把临时钩子安装进用户的常规启动器。

原始导出、私有日志及测试数据库位于忽略目录 `dev-artifacts/authority-probe/`。其中 `loopback-server/result.json` 记录 TLS 检查，`headless-*/result.json`、`events.jsonl` 和 `game.log` 记录各次启动探针。原始导出和真实身份信息不加入源码。

## 同机多进程还需要处理的限制

1. `native/host/Program.cs` 当前拒绝在已有游戏进程运行时启动第二个游戏。专用模拟进程和玩家进程共存，需要按角色调整启动检查与进程归属管理。
2. 原生桥接器和游戏适配器使用固定回环端口。多个宿主不能共用同一组监听端口，需要每个实例独立配置并传递给适配器。
3. 玩家与专用进程的身份、缓存和存档目录需要分别配置；不要通过交替修改共享 Steam 配置切换并发实例的身份。
4. 需要分别测量后台进程和玩家客户端的内存、CPU、GPU需求，再决定是否把专用进程迁到第二台机器。

## 已通过的模拟与状态恢复探针

探针实现已纳入 `tools/probe_authority_runtime.py` 与 `tools/authority_probe/`，不依赖归档客户端脚本，也不包含在正式原生客户端包中。运行前校验完整构建指纹，使用新建角色、国度和独立测试数据库。

- 无玩家视图绑定的独立会话进入 Running 状态，确认帧以约 60 Hz 推进。一次持续运行从帧 171 推进到 4609。
- 在模拟线程导出帧 300，完整状态约 94 KB；恢复到同一会话的帧池对象后，校验和一致。
- 通过 ViewAPI 的 EnterLockStep/ExitLockStep 取得同步上下文后调用恢复入口，原会话恢复成功并继续至少 120 帧。直接绕过同步上下文调用会失败。
- 第二个独立会话恢复到源快照原始帧号 300，校验和与源帧完全一致，随后推进到帧 421。游戏内置调试恢复默认保留接收端当前帧号，探针只针对被恢复的目标会话将其改为源帧号。

最近一次第二会话探针的输出目录为 `dev-artifacts/authority-probe/probe-20261106T184442Z-17e510/`。目录名为 UTC 时间；原始数据保持本机私有。

## 已通过的持续状态传输与故障探针

`server/authoritative_state.py` 提供实验性的完整状态包、长度和摘要校验、单槽快照邮箱与每连接独立发送循环。它尚未接入公开网关或默认服务端入口；对工作进程的身份认证与 TLS 接入必须在后续集成层完成。

`--stream` 探针在一个游戏进程内创建一个生产状态的会话和两个独立接收会话。源会话约每 6 个逻辑帧导出完整状态，经 Python 中继和两条独立回环 TCP 通道返回接收端，接收端按源帧号恢复并比较实际模拟校验和。

实测记录 `probe-20261106T185149Z-fe451d`：

- 权威会话持续推进到帧 1200，产生 150 份状态。
- 对接收端 1 丢弃源帧 600 至 779 的 30 份状态，相当于约 3 秒更新中断。该接收端在帧 714 达到 120 帧预测上限并停止自身模拟。
- 另一接收端在故障区间继续接收并校验 30 份状态，权威会话持续推进。
- 接收端 1 恢复更新后接受最新源状态并继续运行。
- 两个接收端分别通过 120 和 148 次状态校验，无脚本或中继错误。

此测试包含真实游戏帧和真实 TCP 字节流，但三个会话共用一个游戏进程；没有验证独立玩家进程、真实输入上行、游戏视图平滑或多人存档。因此报告中的 `networked_state_sync_verified` 仍为 false。

另有自动化测试使用不读取数据的连接验证：发送写入阻塞时，生产者仍能发布新状态，健康连接仍能收到最新状态；旧的完整状态在单槽队列中被覆盖，队列不会无限增长。该策略仅适用于可独立恢复的完整状态，不能用于丢弃输入命令、交易或一次性事件。

## 重现

在 Windows 开发环境安装依赖：

```powershell
uv pip install --python .venv/Scripts/python.exe --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r tools/authority_probe/requirements.txt
.venv/Scripts/python.exe tools/probe_authority_runtime.py --seconds 120 --replicas 2
.venv/Scripts/python.exe tools/probe_authority_runtime.py --seconds 120 --stream
```

游戏路径从忽略文件 `workspace.local.json` 读取，也可通过 `--game-directory` 显式传入。已有游戏进程运行时探针会拒绝启动。探针结束后关闭自己创建的游戏进程、临时后端和回环连接，结果写入新的私有运行目录。

## 下一项可验证目标

在独立运行时缺少游戏专用构造链的情况下，当前可用的测试路径是房主权威：房主电脑运行一个带视图的真实游戏进程，第二台电脑运行一个玩家进程，通过开发入口交换完整权威帧和第二玩家的 47 字节输入。`tools/play_authority_lan.py` 提供局域网 host/join 原型，固定远端玩家槽位为 1，校验构建指纹、加入码、快照摘要和输入序号；默认关闭合成移动，保留键鼠/手柄输入。该原型尚未接入正式启动器、正式 Photon 网关、多人存档或公网 TLS。

本机回环烟测已观察到房主和客机视图均进入 `clientFlow=4`，客机连续通过 693 次完整状态校验，房主收到客机输入和命令流。由于烟测使用两份完整零售游戏进程，不能作为 2 核 4 GB 服务器资源验收，也不能替代两台实体电脑的人工移动、战斗、交互和断线重连验收。

工具参考：[Il2CppDumper 官方说明](https://github.com/Perfare/Il2CppDumper)、[Frida JavaScript API](https://frida.re/docs/javascript-api/)、[Unity 6000.1 启动参数](https://docs.unity3d.com/6000.1/Documentation/Manual/PlayerCommandLineArguments.html)。


## 2026-11-07 接续复测与双机交付入口

已移除独立运行器实验中替换原生工厂、按猜测布局构造 MoonQuantumGame 的回调补丁，保留通用 SessionRunner 的异常诊断。GUI 静态构造绕过仍仅作用于经过完整指纹校验的隔离诊断子进程，不写游戏文件。再次独立加载得到 30,406 个数据库资源对象，未加载 UnityPlayer；DotNetSessionContext 的资源计数仍为 0，不能据数据库反序列化成功推断游戏专用资源解析上下文已经就绪。记录为 `dev-artifacts/authority-probe/standalone-20261107T072140Z-a954f1/`。

通用运行器复测仍记录 QuantumGame/MoonQuantumGame 类型转换异常，随后为空引用，没有获得可推进的真实会话。证据为 `standalone-20261107T072212Z-281d3d/`。headless 入口明确报告 `real_simulation_verified=false`、`dedicated_server_ready=false`，不作为低配服务器交付。

修正局域网开发入口的畸形帧检查、协议/回调错误记录、客机视图就绪记录、欢迎消息版本校验和状态帧倒退检查；房主先绑定端口再启动游戏，端口占用时不会先拉起游戏。

新一轮回环记录位于 `dev-artifacts/authority-lan-review-20261107/`：两端均进入角色视图，客机通过 376 次完整帧校验，房主收到 1,826 条输入和 12 条命令，报告均无错误，游戏日志无异常。客机退出后，房主继续推进到帧 5909。测试未启用合成移动，未进行人工游玩；收到输入或命令不证明攻击、拾取等具体操作效果正确。测试游戏进程已清理。

完整 Python 测试共 150 项，147 通过、3 项私有样本测试跳过。两台实体电脑的准备、启动、加入和人工验收步骤见 [双机局域网测试](AUTHORITY-LAN-TEST.md)。当前是可供尝试的开发原型，尚未纳入正式启动器或 Release。
