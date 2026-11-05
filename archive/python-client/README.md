# 原 Python/Frida 客户端（归档）

这里保存迁移到原生 EXE/DLL 之前的 Python 客户端、JavaScript 适配器、WinForms 界面源码和启动脚本。源码保留，当前客户端不加载它们。

- `client/`：原入口、身份绑定、GSE 安装、TLS 桥接、JavaScript 游戏适配器及旧界面源码。
- `tls_transport.py`：原 Python 客户端使用的 TLS 实现快照；维护中的服务端另有 `server/tls_transport.py`。
- `tools/build_public_release.py`：历史打包脚本，保留原目录假设，供阅读与追溯；不作为当前构建入口。
- `*.cmd`、`WickedLauncher.exe.config`：原部署入口与配置。

这些文件按原实现归档，不能把本目录直接当成完整可运行客户端：旧运行环境和已编译 EXE/DLL 均不在源码仓库。需要历史可运行包请查看 [v0.1.3 Release](https://github.com/Necthus/wicked-private-realms/releases/tag/v0.1.3)。当前推荐 [最新原生 Release](https://github.com/Necthus/wicked-private-realms/releases/latest)。

测试套件仍对原 Python 实现进行回归，并用它作为原生网络测试的参考。
