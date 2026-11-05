# Wicked Private Realms

《恶意不息 / No Rest for the Wicked》v29466 的实验性私有国度服务端与 Windows 启动器。服务端和客户端分别下载、分别配置。客户端使用服务器地址、加入密码、SteamID64 直接通过 TLS 连接，不使用 SSH 私钥。

## 下载与教程

当前推荐版本为 [v0.2.3 正式版（Latest）](https://github.com/Necthus/wicked-private-realms/releases/latest)。客户端已全面迁移到编译后的 WinForms 启动器、原生网络宿主和注入 DLL，下载约 2.5 MiB，不再携带 Python、Frida、JavaScript 或 runtime 目录。服务端继续使用 v0.1.4 Python 实现。

| 文件 | 用途 | 教程 |
|---|---|---|
| `Wicked-Private-Realms-Client-Native-v29466-v0.2.3.zip` | 玩家完整原生客户端，含启动器、宿主、适配 DLL 和 GSE | [客户端安装](docs/CLIENT.md) |
| `Wicked-Private-Realms-Server-v0.1.4.zip` | 独立服务端；配置加入密码后运行 | [服务端部署](docs/SERVER.md) |
| `SHA256SUMS.txt` | 校验两个下载包 | — |

两个包在同一 Release 中分别提供。已有 v0.1.4 服务端无需升级或转换数据库。客户端需要 Windows x64 与 .NET Framework 4.8；从旧 Python 客户端升级时请完整替换客户端目录，见 [迁移与回退](docs/NATIVE-MIGRATION.md)。

原 Python 客户端源码完整保留，作为历史实现、协议参考和故障排查资料；当前发布与维护的客户端使用原生实现。Python 仍用于服务端以及开发、构建和自动测试。

服务端不需要游戏文件或 Steam。默认只开放 TCP 8443；支持 Docker 或 Python 部署，首次启动自动建立数据库和持久 TLS 证书。世界和角色保存在服主的数据目录；角色按 Steam ID 归属。

客户端默认安装随包 GSE 兼容层，把其他模拟版统一到已核对的 GSE Steam API；替换前备份原 DLL/设置。随包完整 `steam_settings` 使用去个人信息的模板，Steam ID 由玩家填写。正版可取消 GSE 选项，但仍要求匹配构建，正版游玩兼容性未全面验证。

v0.1.4 修复多人聊天中发送者收到额外回送而显示两次的问题；已有客户端无需更新，服主更新服务端并重启。

v0.1.3 另修复便携 Python 隔离环境下的 steam_identity/client_identity 导入错误；无需另装 Python。

v0.1.2 修复进入国度时的 Photon 玩家属性通知路由：17 位 Steam ID 昵称不会再被误当作房间最大人数。升级已有服务器后重启即可，客户端无需为该修复重装。

## 工作方式与验证范围

```text
客户端游戏 ─ 本机适配器 ─ TLS 8443 ─ 私有国度服务端
                                      ├─ Steam ID / 加入密码 / 成员
                                      ├─ Photon / Quantum 输入与快照
                                      └─ 世界、角色和历史存档
```

游戏模拟由客户端执行；服务端排序确认输入、协调快照与存档。国度最多 4 人。单成员的远端国度保持在线共享；本地离线国度仍由游戏原流程处理。游戏分辨率、全屏和画质设置不被启动器覆盖。

原 Python 实现已完成双客户端同国度互动和保存验证。原生客户端通过初始化、独立数据库入场与保存回归、15 项宿主自检和 14 项网关/原生网络测试；维护者已在当前游戏环境手动测试并确认正常。此结论不代表所有硬件、游戏流程、正版环境或长期运行场景均已覆盖。

适配器针对记录的 v29466 GameAssembly.dll 精确哈希，其他构建会拒绝注入。不同资源或模组也可能造成确定性模拟分歧。当前 Steam ID 是私服账号索引，不进行真实 Steam 所有权验证。客户端首次连接使用证书指纹首次信任，之后固定证书；详见部署教程。

## 源码与开发

* 服务端入口：`run_private_server.py`；配置示例：`deploy/server-config.example.json`。
* 当前客户端：`client/launcher.cs`（WinForms）、`native/host/*.cs`（网络与进程管理）、`native/adapter/adapter.c`（游戏适配）。
* 原 Python 客户端：`client/connect_and_launch.py`、`client/launch.py`、`client/*.js` 等，保留作历史实现；原生包不加载这些文件。
* 开发源码与游戏安装目录分开；使用 `tools/deploy_native_client.py` 从 Release ZIP 部署游戏客户端。
* 随包 GSE：`plugin/gse`，DLL 原始 SHA-256 为 `723e13f5a2e1ec799f342612a3294229ca832d143fdea85c00025216b6696e88`。
* 构建和测试：[开发说明](docs/DEVELOPMENT.md)。

仓库和 Release 不包含游戏 EXE、GameAssembly.dll、游戏资源、真实国度/角色存档、SSH 私钥或服主密码。维护中的主存档数据库和运行日志也不在仓库内。

项目源码采用 MIT；GSE、Python、Frida 等组件保留各自许可证，见 [THIRD_PARTY.md](THIRD_PARTY.md)。这是独立实验项目，与 Moon Studios 或 Valve 无隶属关系。
