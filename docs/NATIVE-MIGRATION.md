# 原生客户端安装与迁移

当前正式版为 v0.2.4。新建和进入纯本地国度时保持本地保存，选择“共享”后才上传；同时移除强制跳过序章的覆盖，由游戏决定是否播放。已有角色、国度和教程完成状态保持。详见 [本地国度](LOCAL-REALMS.md) 与 [序章恢复](PROLOGUE-RESTORE.md)。

客户端已改为编译产物，不携带 Python、Frida、JavaScript 或 runtime 文件夹。服务端仍使用现有 v0.1.4 Python 实现，本次不要求迁移世界数据或升级服务器。

## 安装与使用

1. 关闭旧启动器和游戏，将原 wicked-client 文件夹改名保留备份。
2. 将本原生包中的整个 wicked-client 放在 NoRestForTheWicked.exe 旁。不要把新旧包混合，以便确认没有调用旧运行环境。
3. 运行 WickedLauncher.exe。填写服务器地址（默认 TLS 8443，可填 IP:端口）、加入密码和 17 位 Steam ID。
4. 模拟版保持 GSE 选中；正版可取消，但仍必须匹配 v29466 构建与实际 Steam ID。检查连接通过后启动游戏。
5. 游戏运行期间保持启动器打开；先在游戏中返回菜单保存后再退出。“停止”或直接关闭启动器会结束本次游戏进程，未确认保存可能丢失。

需要 Windows x64 和 .NET Framework 4.8（Windows 10/11 通常已自带）。不需要 Python、Frida、SSH 密钥或管理员级 HTTP 端口授权。本机端口 18769/18770 仍需空闲；两个客户端不能同时占用同一机器的这两个端口。

Windows 可能为新 EXE 弹出防火墙提示。客户端只监听 127.0.0.1，向服务器发起出站 TLS；不用开放客户端公网入站端口。不要关闭系统安全功能。服务器仍需按服务端教程放行 TLS 端口。

## 包内结构

* WickedLauncher.exe：WinForms 界面，启动编译后的主机。
* WickedNativeHost.exe：Steam/GSE 配置与身份读取、证书指纹检查、TLS API 和二进制桥、游戏进程生命周期。
* WickedAdapter.dll：静态链接 MSVC 运行库与 MinHook 的 x64 DLL；游戏进程内安装 IL2CPP/Photon 适配钩子。
* plugin/gse：既有已核对 DLL 和全部 8 个设置模板；替换其他模拟器前会备份原文件。同一 GSE 上的个人配置保持原值，仅绑定 Steam ID。

主机会完整校验已记录的 GameAssembly SHA-256，挂起游戏并使用 Windows 加载器加载适配 DLL，再调用它的初始化导出；确认共享内存握手成功后才恢复主线程。会话通过仅当前用户/System 可访问的共享内存传递，不写入参数或磁盘。DLL 再检查 PE 布局。失败则结束本次创建的游戏进程。

证书首次信任（TOFU）路径与旧 Python 客户端相同；后续证书变化时拒绝连接。Steam ID 仍是私服账号索引，不是 Steam 所有权认证。图形设置和 Steam ID 本地存档路径不覆盖。

## 日志与已验证内容

* research/native-adapter.jsonl：本次原生 DLL 的初始化、钩子安装、入场状态。
* research/native-game.log：实际原生会话的 Unity 日志，不与旧 test-player.log 混用。
* 启动器日志显示 Native adapter initialized before game resume 和 hooks installed，表示初始化握手成功。

已完成：15 项编译主机自测、DLL 自测（v0.2.4 完整掩码为 0x7F）、原生主机对 Python 网关的密码/证书/HTTP/2 MiB 双向流测试、实际游戏登录与主菜单。使用备份数据库的本地测试服务，已通过游戏原有流程进入在线场景，确认玩家第 120 帧加入、持续确认帧、两次定时保存、返回菜单时最终保存和玩家移除。关闭主机后本次游戏进程被自动回收。测试后恢复本地存档与 GSE 设置；生产国度未参与此次游戏测试。

开发测试用 Python/Frida 读取状态并在主更新线程调用游戏原有入场/退出流程，未用 Frida 替代原生适配功能；这些开发工具不进入客户端包。随后维护者在当前游戏环境手动测试并确认正常，现发布为正式版。完整的所有流程、硬件与长期运行覆盖并未据此承诺。v0.2.3 沿用该测试版本的三个 EXE/DLL 二进制，仅更新发布说明与版本元数据。

## 回退

退出原生游戏与启动器，将本文件夹移开，恢复之前保留的 wicked-client（或重新下载 v0.1.3 完整客户端）。不要让旧包目录中残留 WickedNativeHost.exe，否则混合版启动器可能优先选择原生主机。世界与角色仍在原服务器，不需要转换数据库。

## 开发构建

源代码在 native/host/*.cs、native/launcher/*.cs 和 native/adapter/adapter.c。Windows 上安装 Visual Studio Build Tools 与 Windows SDK 后，可用开发机 Python 构建（Python 只用于构建脚本，不在发布包中）：

```powershell
python native/build_native.py
python native/build_host.py
python native/build_launcher.py
python native/build_selftest.py
native/dist/WickedNativeHost.exe --self-test
```

运行 `python native/build_native_release.py --gse-from <已发布原生客户端ZIP>` 在 `releases` 下生成客户端 ZIP。部署到游戏目录使用 `python tools/deploy_native_client.py --game-directory <游戏目录> --release <客户端ZIP>`；开发目录无需放在游戏旁。MinHook v1.3.4 源码与许可证随仓库提供。

原 Python 客户端仍完整保留在 `archive/python-client/` 中，作为历史实现和协议参考；当前原生客户端不加载这些脚本。服务端继续使用 Python。构建工具使用开发机 Python，用户安装的客户端不需要 Python 或 Frida。

审计记录：早期 v0.2.1-native 的游戏成功说明误读了旧日志，已撤回到草稿，不应使用。v0.2.2-native 新增显式初始化握手、独立日志、完整 SHA 校验、当前用户共享内存 ACL、进程 job 回收、证书拒绝测试，并使用独立数据库重新验证。

## v0.2.4 验证与升级

本版本使用已在本机安装验证的三个客户端二进制。已验证本地世界转换、重读、本地会话启动、周期和退出保存；在隔离服务端验证主动调用原共享流程后才创建在线国度。输入参数转发和纯本地条目判断纳入 DLL 自检。

服务器继续使用现有 v0.1.4，无需升级。已有 v0.2.x 原生客户端可在关闭游戏后用新包覆盖程序与文档，并保留 `data/launcher-settings.json`、`data/server-trust`。首次从旧 Python 客户端升级仍按上文完整替换目录。

此修复阻止后续自动上传，不会自动找回所有曾误上传的国度。需要恢复旧世界时先备份并按本地国度文档核对数据来源。本次 Release 仍支持四人联机，未包含五人实验改造。
