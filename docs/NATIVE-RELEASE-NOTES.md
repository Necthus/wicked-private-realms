Native client candidate v0.2.1-native：编译产物客户端，不携带 Python、Frida 或 runtime 目录。

包含：WinForms WickedLauncher.exe、WickedNativeHost.exe、WickedAdapter.dll、当前已核对 GSE DLL 与完整 steam_settings。NativeHost 负责 TLS 登录、回环 API/Photon 桥接、挂起游戏、注入 DLL；Adapter 负责 v29466 的原生 IL2CPP/Photon hooks、Steam ID/存档路径校验、单成员共享国度和心跳修复。

本机完成：MSVC x64 编译、WickedAdapterSelfTest=0x1F、原生 host `--check` 公网登录、实际 v29466 游戏启动、在线模拟、玩家加入帧 120、确认输入和多个保存周期。原 Python 客户端与 v0.1.x 仍可作为回退。

这是候选版本，发布前仍需完整游戏级回归：移动、战斗、聊天、双机同步、地图切换、返回菜单、重进、断线恢复和长时间运行。原生 host/adapter 只支持记录的 GameAssembly.dll 版本哈希，不修改游戏 DLL。
