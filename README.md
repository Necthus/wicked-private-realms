# Wicked Private Realms

《恶意不息 / No Rest for the Wicked》v29466 的实验性私有国度服务端与 Windows 启动器。服务端和客户端分别下载、分别配置。客户端使用服务器地址、加入密码、SteamID64 直接通过 TLS 连接，不使用 SSH 私钥。

## 下载与教程

从 [Releases](https://github.com/Necthus/wicked-private-realms/releases) 下载：

| 文件 | 用途 | 教程 |
|---|---|---|
| `Wicked-Private-Realms-Server-v0.1.3.zip` | 自建服务器；配置加入密码后运行 | [服务端部署](docs/SERVER.md) |
| `Wicked-Private-Realms-Client-v29466-v0.1.3.zip` | 玩家完整客户端，含启动器、Python、Frida 和 GSE | [客户端安装](docs/CLIENT.md) |
| `Wicked-Private-Realms-Client-Update-v29466-v0.1.3.zip` | 已有完整客户端的覆盖更新，不含运行环境 | [客户端安装](docs/CLIENT.md) |
| `Wicked-Private-Realms-Client-Import-Fix-v0.1.3.zip` | 修复已有客户端的便携 Python 导入错误，只含启动脚本 | [客户端安装](docs/CLIENT.md) |
| `SHA256SUMS.txt` | 校验下载内容 | — |

服务端不需要游戏文件或 Steam。默认只开放 TCP 8443；支持 Docker 或 Python 部署，首次启动自动建立数据库和持久 TLS 证书。世界和角色保存在服主的数据目录；角色按 Steam ID 归属。

客户端默认安装随包 GSE 兼容层，把其他模拟版统一到已核对的 GSE Steam API；替换前备份原 DLL/设置。随包完整 `steam_settings` 使用去个人信息的模板，Steam ID 由玩家填写。正版可取消 GSE 选项，但仍要求匹配构建，正版游玩兼容性未全面验证。

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

既有双客户端已实际验证同国度互动和保存流程。当前新增的 TLS 路径覆盖密码拒绝、证书固定、二进制转发及独立服务端启动测试；跨公网登录已验证。完整双机游玩仍应按当前 Release 重新回归，不应把 API 成功当作所有游戏流程已验证。

适配器针对记录的 v29466 GameAssembly.dll 精确哈希，其他构建会拒绝注入。不同资源或模组也可能造成确定性模拟分歧。当前 Steam ID 是私服账号索引，不进行真实 Steam 所有权验证。客户端首次连接使用证书指纹首次信任，之后固定证书；详见部署教程。

## 源码与开发

* 服务端入口：`run_private_server.py`；配置示例：`deploy/server-config.example.json`。
* 客户端入口：`client/launcher.cs` / `client/connect_and_launch.py`。
* 随包 GSE：`plugin/gse`，DLL 原始 SHA-256 为 `723e13f5a2e1ec799f342612a3294229ca832d143fdea85c00025216b6696e88`。
* 构建和测试：[开发说明](docs/DEVELOPMENT.md)。

仓库和 Release 不包含游戏 EXE、GameAssembly.dll、游戏资源、真实国度/角色存档、SSH 私钥或服主密码。维护中的主存档数据库和运行日志也不在仓库内。

项目源码采用 MIT；GSE、Python、Frida 等组件保留各自许可证，见 [THIRD_PARTY.md](THIRD_PARTY.md)。这是独立实验项目，与 Moon Studios 或 Valve 无隶属关系。
