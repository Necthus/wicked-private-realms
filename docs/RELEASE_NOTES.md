v0.1.3 修复客户端启动时的 `ModuleNotFoundError: No module named steam_identity`。

Windows 便携 Python 的 _pth 隔离模式不会自动搜索脚本目录；此前入口依赖这个隐式路径。新版增加 client 包并显式注册根路径，同时修复连接、GSE 安装和游戏注入三个入口。

已有 v0.1.x 客户端优先下载 **Wicked-Private-Realms-Client-Import-Fix-v0.1.3.zip**：关闭启动器，把 ZIP 内 wicked-client 文件夹放到游戏 EXE 所在目录，合并替换原同名文件夹即可。不需要重新安装 Python，不改变 Steam ID、存档或服务器。新用户下载完整 Client ZIP。

验证：用 v0.1.2 实际完整压缩包在临时目录复现原错误；应用修复后，压缩包自带 Python 成功运行 connect_and_launch.py、install_gse.py、launch.py 三个入口。完整测试 92 项通过，公开源码中 3 项未分发私有原始样本测试会显式跳过。

服务端行为沿用 v0.1.2 的 Photon 玩家属性通知修复，已部署服务器无需为本次客户端导入修复重启。此处也提供同版本服务端包，方便新部署。

服务端教程：https://github.com/Necthus/wicked-private-realms/blob/main/docs/SERVER.md

客户端教程：https://github.com/Necthus/wicked-private-realms/blob/main/docs/CLIENT.md

完整客户端继续包含已核对的 GSE DLL、全部 8 个设置文件、Python 运行环境和 Frida 17.24.3。无 SSH 私钥、服务器密码或真实存档。仅支持记录的 v29466 构建；证书首次信任和 Steam ID 索引的限制见教程。
