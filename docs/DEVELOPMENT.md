# 开发与构建

## 测试

Python 3.11+：

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -p 'test_*.py' -q
```

测试包含 TLS 登录与流转发、独立服务端初始化、Steam ID 绑定、GSE 安装完整性与备份、协议编码、共享输入、快照和事务存档。公共仓库只保留合成协议向量，不分发真实存档和原生会话捕获；3 项依赖这些私有原始样本的测试会显式跳过。共享房间测试使用匿名的合成配置模板。

## 当前原生客户端

客户端已全面迁移到编译后的 EXE/DLL。`client/launcher.cs` 是当前 WinForms 界面，`native/host` 是网络及进程宿主，`native/adapter` 是游戏适配 DLL，`native/vendor/minhook` 是静态链接的依赖源码。

开发目录与游戏安装目录分开。Windows 开发机需要 Visual Studio Build Tools、Windows SDK、.NET Framework 4.8 和 Python。仓库根目录执行：

```powershell
python native/build_native.py
python native/build_host.py
python native/build_launcher.py
python native/build_selftest.py
native/dist/WickedNativeHost.exe --self-test
python native/test_native_host.py
python native/build_native_release.py
```

原生 DLL 和宿主输出到 `native/dist`；启动器输出到根目录。发布包输出到 `releases/public-v0.2.3`。用户客户端不包含构建工具或 Python 环境。

将发布包部署到游戏目录：

```powershell
python tools/deploy_native_client.py --game-directory <游戏目录> --release <客户端ZIP>
python tools/deploy_native_client.py --game-directory <游戏目录> --release <客户端ZIP> --check
```

部署保留已有启动器设置、证书信任记录及日志。首次从旧 Python 版升级应先移走旧客户端目录，避免混用。也可在本地 `workspace.local.json` 设置 `game_directory` 和 `native_release`，该文件不提交 Git。

保留 `WickedLauncher.exe.config`，`WickedLauncher.exe --smoke-test` 可短暂打开界面并输出 DPI 诊断。发布时 `--binaries-from <已测原生ZIP>` 可复用其三个二进制，并逐一核对包内 SHA-256；普通构建默认使用本地编译结果。更新版本前修改打包脚本的 VERSION。

## 保留的 Python 实现

原 Python 客户端源码（`client/connect_and_launch.py`、`client/launch.py`、身份绑定及网络模块、JavaScript 适配代码）继续保留，用于历史参考、协议研究与故障排查。它们不参与当前原生客户端运行。`client/launcher.cs` 为当前和历史方案共用的界面源码；服务端仍使用 Python。

`tools/build_public_release.py` 保留用于服务端及旧 Python 客户端打包。旧完整客户端构建仍需 Python 嵌入式环境和 Frida；这些依赖仅属于历史实现，当前原生 Release 不需要。服务端包与客户端包始终分开提供。

GSE DLL 保持原始字节；配置模板去除了个人身份和本机路径。不要把游戏主程序、其他来源的 DLL、真实存档、私钥或本地开发配置放入公共源码目录。
