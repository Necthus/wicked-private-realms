原生客户端候选 v0.2.2-native：客户端已迁移到编译后的 EXE/DLL，不再携带 Python、Frida、JavaScript 或 runtime 文件夹。服务端保持现有 v0.1.4，无需转换账号或存档。

包含 WickedLauncher.exe（WinForms x64）、WickedNativeHost.exe（.NET Framework 4.8）和 WickedAdapter.dll（MSVC x64 /MT + MinHook），以及原有已核对 GSE 与全部设置模板。约 2.5 MiB。

已验证：DLL 自测 0x1F、编译主机 15 项自测、原生主机对 Python 网关的密码/证书变更拒绝、HTTP 和 2 MiB 双向转发测试。实际游戏由主机挂起启动，通过 Windows 加载器注入，并显式调用初始化导出；握手确认后才恢复主线程。随后独立 DLL 日志确认 17 个适配钩子生效、私服登录、玩家第 120 帧加入及持续确认输入。

在生产数据库的本地副本上，通过游戏原有入场/退出流程完成两次定时保存、返回菜单最终保存、玩家移除；关闭主机自动回收游戏。开发工具仅用于调度正常游戏流程和读状态，不进入客户端包。正式阿里云国度未用于这轮游戏测试。

请独立解压本包为游戏 EXE 旁的 wicked-client，避免与旧 runtime 混合。填写服务器、密码、Steam ID。仍需完整测试手动移动、战斗、双机同步、聊天、地图切换、重连及长期运行；保留 v0.1.3 客户端作回退。

首次 TLS 证书采用指纹首次信任，后续固定；Steam ID 只是私服索引。没有更改 Windows 安全设置。系统可能为新 EXE 弹出防火墙提示，本地只监听 127.0.0.1，不要求开放客户端公网入站。

早期 v0.2.1-native 的游戏成功说明误读旧日志，已撤回到草稿；请勿使用该旧包。本候选重新实现了初始化握手、独立日志、完整 SHA 校验、共享内存 ACL、job 进程回收及证书拒绝测试。

使用与回退：https://github.com/Necthus/wicked-private-realms/blob/main/docs/NATIVE-MIGRATION.md
