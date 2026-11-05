# v0.2.5 — 64 位 GC 句柄修复（四人正式版）

修复 v29466 原生适配层将 IL2CPP GC 句柄保存在 32 位变量中的问题。该游戏的句柄是指针宽度；现在分配、缓存、传递和释放均使用 uintptr_t，防止临时托管对象释放时地址截断而崩溃。新增大于 UINT32_MAX 的句柄回归检查，完整 DLL 自检掩码仍为 0x7F。

本版本包含 v0.2.4 的全部修复：本地国度只在主动共享后上传，序章资格交由原游戏判断。仍保持四人上限，未包含五人实验代码。

分别下载：

- `Wicked-Private-Realms-Client-Native-v29466-v0.2.5.zip`：完整原生客户端，含启动器、宿主、适配 DLL、GSE 及设置模板。
- `Wicked-Private-Realms-Server-v0.1.4.zip`：与此前发布的服务端包字节一致，服务器无需升级。
- `SHA256SUMS.txt`：两个包的 SHA-256。

已有原生客户端在关闭游戏和启动器后覆盖新包程序与文档，保留 `data/launcher-settings.json` 与 `data/server-trust`。客户端限定 Windows x64、.NET Framework 4.8 及匹配的 v29466 游戏。

以下保留 v0.2.4 修复说明，描述的本地国度和序章行为均继续有效。

---

# v0.2.4 — 本地国度与序章流程修复

修复通过启动器登录后，新建本地国度在进入时被自动上传并变为在线的问题。纯本地国度现在保持本地运行和保存，只有主动选择游戏中的“共享”才执行原上传流程。已有共享国度继续在线运行。

同时移除强制跳过船上序章的覆盖，原样转发游戏提供的序章与重生参数。游戏仍会按角色和进度决定序章资格；本次修复不会重置教程完成状态或强制重复播放序章。

下载分别提供：

- `Wicked-Private-Realms-Client-Native-v29466-v0.2.4.zip`：完整原生客户端，约 2.5 MiB，含 WinForms 启动器、网络宿主、适配 DLL、GSE 与设置模板。
- `Wicked-Private-Realms-Server-v0.1.4.zip`：与此前发布的服务端包字节一致，现有服务器无需升级或转换数据库。
- `SHA256SUMS.txt`：上述两个包的校验值。

验证：DLL 自检 0x7F，覆盖入场全部参数转发及本地/远端/合并存档分支；宿主 15 项自检；14 项原生网络回归。实际游戏完成本地世界转换、重读、会话启动、周期保存与返回菜单保存；隔离服务器确认主动共享后才创建在线国度。完整五客户端测试不在本次范围内，本版本仍保持四人上限。

客户端二进制复用本机已安装验证的本地国度修复版。源码保留既有目录结构，Python 客户端归档仍在 `archive/python-client/`，EXE/DLL 仅在 Release 提供。

更新已有 v0.2.x 原生客户端：退出游戏与启动器，覆盖新包程序和文档，保留 `data/launcher-settings.json` 与 `data/server-trust`。从旧 Python 包迁移请先完整移走旧目录再解压新包。需要 Windows x64、.NET Framework 4.8 和匹配的 v29466 游戏。

本地国度不上传，不代表已有云端角色停止同步。补丁阻止后续自动上传，不会自动找回所有曾误上传的国度；恢复旧世界前请先备份，保留最新内容。

教程：[客户端安装](https://github.com/Necthus/wicked-private-realms/blob/main/docs/CLIENT.md) · [本地国度](https://github.com/Necthus/wicked-private-realms/blob/main/docs/LOCAL-REALMS.md) · [序章恢复](https://github.com/Necthus/wicked-private-realms/blob/main/docs/PROLOGUE-RESTORE.md) · [服务端部署](https://github.com/Necthus/wicked-private-realms/blob/main/docs/SERVER.md)
