# 开发与构建

## GitHub Actions CI/CD

仓库通过 `.github/workflows/ci.yml` 统一处理 `push`、`pull_request` 和手动 `workflow_dispatch`。首次 fork 后需要在仓库 Actions 页面启用工作流；本仓库已启用。

每次运行会执行 Linux/Windows 单元测试、MSVC x64 DLL 编译与自检、.NET 宿主/启动器编译及原生网络回归，并生成完整客户端 ZIP 与独立服务端 ZIP。通过测试后，可在运行页面的 Artifacts 中下载 `native-client` 和 `standalone-server`（保留 14 天）；客户端 ZIP 内的 `native/BUILD-METADATA.json` 记录包版本与源码 commit。

普通分支构建的客户端版本为 `v0.2.5-ci.<运行号>.<重试号>`。从版本标签运行时使用标签作为客户端版本，例如 `v0.2.6` 或 `v0.2.6-rc.1`。服务端继续使用独立版本 `v0.1.4`。

CI 通过 `tools/fetch_ci_gse.py` 下载上游固定 `v0.2.5` 客户端包并校验整个 ZIP 的固定 SHA-256，只从中提取 GSE。客户端三个 EXE/DLL 均使用本次源码重新编译的产物；打包脚本还会核验 GSE DLL 的既有固定哈希。上游包内容发生变化会使构建失败，需要核对后显式更新固定校验值，不自动跟随 Latest。

发布新版本：

```powershell
git tag v0.2.6
git push origin v0.2.6
```

只有 `v*` 标签的 push 会自动发布 Release，并且必须等待测试、原生构建和服务端打包全部成功。版本格式为 `v主版本.次版本.补丁版本`，可追加 `-rc.1` 等预发布后缀。含 `-` 的标签发布为 prerelease；其他版本标记为 Latest。每个 Release 包含两个 ZIP 和汇总的 `SHA256SUMS.txt`。上传先写入草稿，所有资产成功上传后再公开；已发布的同名 Release 不会被覆盖。

普通 push、PR 和手动运行仅构建产物，不创建 Release。仅发布 job 使用短期 `GITHUB_TOKEN` 的 `contents: write` 权限；测试和构建均为只读，不需要额外配置 PAT 或 SSH/服务器密钥。CD 的范围是发布下载包，不会连接或重启正在运行的私服。自动化测试不代表已完成真实游戏会话验证。

## 测试

Python 3.11+：

```bash
python -m pip install -r tests/requirements.txt
python -m unittest discover -s tests -t . -p 'test_*.py' -q
```

测试包含 TLS 登录与流转发、独立服务端初始化、Steam ID 绑定、GSE 安装完整性与备份、协议编码、共享输入、快照和事务存档。公共仓库只保留合成协议向量，不分发真实存档和原生会话捕获；3 项依赖这些私有原始样本的测试会显式跳过。共享房间测试使用匿名的合成配置模板。

## 当前原生客户端

客户端已全面迁移到编译后的 EXE/DLL。`native/launcher/launcher.cs` 是当前 WinForms 界面，`native/host` 是网络及进程宿主，`native/adapter` 是游戏适配 DLL，`native/vendor/minhook` 是静态链接的依赖源码。

开发目录与游戏安装目录分开。Windows 开发机需要 Visual Studio Build Tools、Windows SDK、.NET Framework 4.8 和 Python。仓库根目录执行：

```powershell
python native/build_native.py
python native/build_host.py
python native/build_launcher.py
python native/build_selftest.py
native/dist/WickedNativeHost.exe --self-test
python native/test_native_host.py
python native/build_native_release.py --gse-from <已发布原生客户端ZIP>
```

原生 DLL、宿主和启动器统一输出到 `native/dist`。启动脚本和 .config 源文件在 `native/launcher/`。发布包输出到 `releases/public-v0.2.5`。用户客户端不包含构建工具或 Python 环境。

将发布包部署到游戏目录：

```powershell
python tools/deploy_native_client.py --game-directory <游戏目录> --release <客户端ZIP>
python tools/deploy_native_client.py --game-directory <游戏目录> --release <客户端ZIP> --check
```

部署保留已有启动器设置、证书信任记录及日志。首次从旧 Python 版升级应先移走旧客户端目录，避免混用。也可在本地 `workspace.local.json` 设置 `game_directory` 和 `native_release`，该文件不提交 Git。

`WickedLauncher.exe.config` 源文件在 `native/launcher/`，打包时放在 EXE 旁。`WickedLauncher.exe --smoke-test` 可短暂打开界面并输出 DPI 诊断。发布时 `--binaries-from <已测原生ZIP>` 可复用其三个二进制，并逐一核对包内 SHA-256；普通构建默认使用本地编译结果。更新版本前修改打包脚本的 VERSION。

## 服务端权威模拟实验

`codex/server-authoritative-state-sync` 分支的开发探针用于核对真实游戏运行时、快照恢复和独立状态流。复现命令、已通过的实验与验证范围见 [本机可行性记录](AUTHORITY-FEASIBILITY.md)。`tools/authority_probe/` 中的 JavaScript/Frida 仅用于开发验证，不随正式原生客户端分发。实验性的 `server/authoritative_state.py` 尚未接入默认运行入口。

无图形诊断使用 `python tools/probe_authority_runtime.py --stream --authority-only --player --nographics --seconds 60`。报告分别记录 `nographics_requested` 和 `null_graphics_device_observed`；启用 Null Device 不代表真实模拟已运行。当前 v29466 会在音频相关的游戏核心初始化阶段失败，独立 headless 权威运行时尚未验证。

直接脱离 Unity 加载 IL2CPP 的诊断使用 `python tools/probe_standalone_runtime.py --skip-imgui-eager-init --load-assets`。它在校验游戏指纹后启动隔离子进程；当前可反序列化 30,406 个资源，但游戏专用会话初始化仍失败，`dedicated_server_ready` 保持 false。`--runner-probe` 只用于记录异常，不是服务器启动入口。

当前可供两台实体电脑尝试的房主模拟入口及人工验收步骤见 [双机局域网测试](AUTHORITY-LAN-TEST.md)。该入口每台电脑运行一个游戏，无需第三个模拟游戏进程；尚未完成两台实体电脑的人工游玩验收。

## 保留的 Python 实现

原 Python 客户端源码（`archive/python-client/client/` 下的入口、身份绑定、网络模块和 JavaScript 适配代码）继续保留，用于历史参考、协议研究与故障排查。它们不参与当前原生客户端运行。`native/launcher/launcher.cs` 为当前和历史方案共用的界面源码；服务端仍使用 Python。

当前服务端源码在 `server/`，使用 `python tools/build_server_release.py` 构建独立服务端 ZIP。历史打包脚本移至 `archive/python-client/tools/build_public_release.py`，原样保留旧目录假设，仅作归档参考。旧完整客户端依赖 Python 嵌入式环境和 Frida；当前原生 Release 不需要。服务端包与客户端包始终分开提供。

GSE DLL 保持原始字节；配置模板去除了个人身份和本机路径。不要把游戏主程序、其他来源的 DLL、真实存档、私钥或本地开发配置放入公共源码目录。

## 源码与二进制分发

源码仓库不包含 EXE、DLL、编译中间文件或 ZIP。构建产物写入 `native/dist/`，打包产物写入 `releases/`，均被 Git 忽略。

GSE 源码与构建说明由 `plugin/gse/UPSTREAM.md` 指向上游。本仓库保留许可证、八个设置模板和 SHA-256 清单，预编译 DLL 只在 Release 客户端包中分发。打包时通过 `--gse-from <原生ReleaseZIP或DLL>` 提供已核对的组件，脚本强制检查固定 SHA-256；它不会从网络自动获取未核对的 DLL。

原 Python 客户端在 `archive/python-client/` 中保留原始文件；测试通过 `tests/__init__.py` 注册归档实现与维护中的服务端导入路径。原生客户端测试会使用归档桥接器作为协议参考，不会将其放进用户客户端包。
