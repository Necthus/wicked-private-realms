# 客户端安装

下载 Release 中的 **Client ZIP**。把整个 `wicked-client` 文件夹放入游戏目录，与 `NoRestForTheWicked.exe` 并列。更新时关闭游戏和启动器，再覆盖同名文件夹。

1. 运行 `wicked-client/WickedLauncher.exe`。
2. 填写服主提供的 **服务器地址、加入密码、自己的 17 位 Steam ID**。不带端口时使用 TCP 8443，也可填 `IP:端口`。
3. 模拟版默认选中“使用随包 GSE 兼容层”。第一次会备份原 Steam API 与设置，再安装随包的已核对 GSE DLL 和完整 `steam_settings`。
4. 点击“检查连接”验证身份和服务器；点击“连接并启动游戏”启动实际客户端。
5. 选择或创建角色，再进入共享国度。正常结束先保存返回菜单，再退出游戏；游戏运行时保持启动器打开。

**不需要 SSH 私钥或额外的服务器连接包。** 完整包内含 Python 与 Frida 17.24.3，无需另装 Python。

## Steam ID 与 GSE

随包 GSE 正是当前已测试的 64 位 DLL，SHA-256：

`723e13f5a2e1ec799f342612a3294229ca832d143fdea85c00025216b6696e88`

它不是另一个 WUS/RUNE 风格附件。完整设置目录在 `plugin/gse/steam_settings`；发布模板移除了个人昵称、固定 Steam ID 和本机绝对存档路径。启动器会把填写的 ID 写入游戏 `steam_settings/configs.user.ini` 的 `account_steamid`，再通过实际 Steam API 核对。

原 DLL 与设置的备份在 `data/gse-backups/`；同一已核对 GSE 安装上的既有设置会保留，修改 Steam ID 前的 INI 也会备份。可手动恢复原 DLL 和设置以撤销 GSE 安装。`Install-GSE.cmd` 提供单独安装入口。

同一 Steam ID 是同一私服账号，两个玩家应使用不同 ID。显示昵称可在 GSE 的 `account_name` 修改；它不决定角色归属。角色卡片名由创建角色时自行填写。

正版可取消 GSE 选项，使用当前 Steam 登录身份；不要把模拟 ID 当作正版身份。适配器仍严格验证 v29466 的 GameAssembly.dll，其他版本会拒绝加载。正版实际游玩兼容性尚未全面验证。

## 存档、TLS 与日志

游戏沿用原版 Steam ID 本地存档路径，并与私服后端记录对应。图形设置不覆盖。远端国度即使只有一名成员也保持共享在线模式；纯本地国度保留原行为。

连接通过 TLS，首次连接会将证书指纹保存在 `data/server-trust`，以后改变会拒绝。首次信任不等同于 CA 认证；可向服主核对启动日志中的指纹。确认服务器确实更换证书后再移除对应信任条目。启动器不保存加入密码，临时游戏会话文件会在退出时清理。

游戏日志：`research/test-player.log`；适配器日志：`research/client-adapter.jsonl`。反馈问题时提供现象和日志中的错误文字，勿发送 `data` 中的会话或服务器私钥。

GSE 为独立的 LGPL 第三方组件，其许可证和来源说明随包提供。项目 MIT 许可证不覆盖 GSE、Python、Frida 或游戏本身。

## 旧包的 ModuleNotFoundError 修复

如果出现 `No module named steam_identity`，可下载 v0.1.3 的 Client-Import-Fix 小补丁。关闭游戏和启动器，将 ZIP 内的 `wicked-client` 文件夹复制到游戏 EXE 所在目录，合并替换已有同名文件夹。无需另装 Python，不覆盖既有运行环境、Steam ID 设置或缓存。也可下载新版完整客户端。

原因是 Windows 嵌入式 Python 的 _pth 隔离配置不自动加入脚本目录；新版入口显式注册客户端根路径并按 client 包导入。三个脚本入口已在实际便携运行环境、与游戏无关的临时工作目录中验证。
