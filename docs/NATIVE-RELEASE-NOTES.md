# v0.2.3 — 原生客户端正式版

客户端已全面迁移到编译后的 WinForms 启动器、网络宿主 EXE 与游戏适配 DLL。完整客户端约 2.5 MiB，不再携带 Python、Frida、JavaScript 或 runtime 目录。原 Python 客户端源码仍完整保留，作为历史实现与协议参考；服务端继续使用 Python。

本次 Release 分别提供：

- `Wicked-Private-Realms-Client-Native-v29466-v0.2.3.zip`：玩家下载，解压到游戏 EXE 旁，运行启动器，填写服务器地址、加入密码与 Steam ID。
- `Wicked-Private-Realms-Server-v0.1.4.zip`：服主下载，配置加入密码、持久数据目录与 TLS 端口；与原 v0.1.4 包字节一致。已有服务器无需升级或转换存档。
- `SHA256SUMS.txt`：上述两个包的 SHA-256。

维护者已对当前原生版本进行游戏内手动测试并确认正常。客户端三个二进制与已测 v0.2.2-native 相同，本次更新文档、版本元数据与正式发布状态。此前通过 DLL 自检 0x1F、宿主 15 项自检、14 项网关/原生网络测试，以及独立数据库的游戏入场、定时保存、返回菜单保存和玩家移除验证。这些验证不代表所有硬件、游戏流程及长期场景均已覆盖。

源码、构建和游戏部署目录分开，新增原生启动器构建与 Release 部署工具。原 Python 实现保留；游戏目录仅需 Release 文件与运行时生成的设置、信任记录和日志。

从旧 Python 包升级：关闭游戏与启动器，将旧 `wicked-client` 移走备份，再完整解压新包；不要合并旧 runtime。可自行迁回 `data/launcher-settings.json` 和 `data/server-trust`。需要 Windows x64 与 .NET Framework 4.8，游戏构建限定 v29466。GSE DLL 与全部设置模板随客户端包提供。

这是非 Pre-release 的正式版本，并设为 GitHub Latest；此前版本全标记为预发布，导致仓库主页没有正常显示正式最新版。

教程：[客户端安装](https://github.com/Necthus/wicked-private-realms/blob/main/docs/CLIENT.md) · [服务端部署](https://github.com/Necthus/wicked-private-realms/blob/main/docs/SERVER.md) · [迁移与回退](https://github.com/Necthus/wicked-private-realms/blob/main/docs/NATIVE-MIGRATION.md)
