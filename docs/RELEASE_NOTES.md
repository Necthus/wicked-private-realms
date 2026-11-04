Native client candidate v0.2.0：将客户端从 Python/Frida 运行时迁移到编译产物。

客户端包只包含 WinForms 启动器、WickedNativeHost.exe、WickedAdapter.dll、GSE 插件与设置，不包含 Python、Frida 或 runtime 目录。

本机验证：MSVC x64 编译、自测 0x1F、原生 TLS 登录、挂起游戏后注入、v29466 在线模拟启动、玩家加入帧 120、持续 confirmed input 和多次服务端保存周期。

这是 native candidate，保留 v0.1.3 Python 客户端作为回退。玩家仍需完成移动、战斗、聊天、双机、地图切换、返回菜单、重进和长时间运行的完整游戏测试。

源码与迁移说明：https://github.com/Necthus/wicked-private-realms/blob/main/docs/NATIVE-MIGRATION.md

服务端仍可使用 v0.1.4。Native 包不含 SSH 私钥、服务器密码、数据库或游戏资源。
