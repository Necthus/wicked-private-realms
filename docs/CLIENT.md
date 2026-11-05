# 客户端安装

下载 [最新正式 Release](https://github.com/Necthus/wicked-private-realms/releases/latest) 中的 **Client-Native ZIP**。把整个 `wicked-client` 文件夹放入游戏目录，与 `NoRestForTheWicked.exe` 并列。更新时关闭游戏和启动器；从旧 Python 版升级请先移走整个旧目录，再解压原生包，避免残留旧运行环境。

1. 运行 `wicked-client/WickedLauncher.exe`。
2. 填写服主提供的 **服务器地址、加入密码、自己的 17 位 Steam ID**。不带端口时使用 TCP 8443，也可填 `IP:端口`。
3. 模拟版默认选中“使用随包 GSE 兼容层”。第一次会备份原 Steam API 与设置，再安装随包的已核对 GSE DLL 和完整 `steam_settings`。
4. 点击“检查连接”验证身份和服务器；点击“连接并启动游戏”启动实际客户端。
5. 选择或创建角色，再进入共享国度。正常结束先保存返回菜单，再退出游戏；游戏运行时保持启动器打开。

**不需要 Python、Frida、SSH 私钥或额外的服务器连接包。** 当前客户端已全面迁移到编译后的 EXE/DLL，需要 Windows x64 和 .NET Framework 4.8。旧 Python 实现保留在源码仓库，当前客户端包不包含它。

## Steam ID 与 GSE

随包 GSE 正是当前已测试的 64 位 DLL，SHA-256：

`723e13f5a2e1ec799f342612a3294229ca832d143fdea85c00025216b6696e88`

它不是另一个 WUS/RUNE 风格附件。完整设置目录在 `plugin/gse/steam_settings`；发布模板移除了个人昵称、固定 Steam ID 和本机绝对存档路径。启动器会把填写的 ID 写入游戏 `steam_settings/configs.user.ini` 的 `account_steamid`，再通过实际 Steam API 核对。

原 DLL 与设置的备份在 `data/gse-backups/`；同一已核对 GSE 安装上的既有设置会保留，修改 Steam ID 前的 INI 也会备份。可手动恢复原 DLL 和设置以撤销 GSE 安装。原生客户端由启动器自动处理 GSE 安装，不使用旧 Python 版的 `Install-GSE.cmd`。

同一 Steam ID 是同一私服账号，两个玩家应使用不同 ID。显示昵称可在 GSE 的 `account_name` 修改；它不决定角色归属。角色卡片名由创建角色时自行填写。

正版可取消 GSE 选项，使用当前 Steam 登录身份；不要把模拟 ID 当作正版身份。适配器仍严格验证 v29466 的 GameAssembly.dll，其他版本会拒绝加载。正版实际游玩兼容性尚未全面验证。

## 存档、TLS 与日志

游戏沿用原版 Steam ID 本地存档路径，并与私服后端记录对应。图形设置不覆盖。远端国度即使只有一名成员也保持共享在线模式；纯本地国度保留原行为。

连接通过 TLS，首次连接会将证书指纹保存在 `data/server-trust`，以后改变会拒绝。首次信任不等同于 CA 认证；可向服主核对启动日志中的指纹。确认服务器确实更换证书后再移除对应信任条目。启动器不保存加入密码；原生宿主通过限制访问的共享内存向适配 DLL 传递游戏会话，不写入磁盘或命令行。

原生游戏日志：`research/native-game.log`；适配器日志：`research/native-adapter.jsonl`。反馈问题时提供现象和日志中的错误文字，勿发送 `data` 中的会话或服务器私钥。

GSE 为独立的 LGPL 第三方组件，其许可证和来源说明随包提供。项目 MIT 许可证不覆盖 GSE、Python、Frida 或游戏本身。

## 旧 Python 客户端

原实现保留在 `archive/python-client/` 中供参考；历史发布包在 v0.1.3 Release。`ModuleNotFoundError: steam_identity` 属于旧 Python 包的导入问题，当前原生包没有该运行依赖。正常升级请使用最新原生完整包，详见 [迁移与回退](NATIVE-MIGRATION.md)。
