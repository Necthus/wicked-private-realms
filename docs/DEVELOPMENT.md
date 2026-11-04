# 开发与构建

## 测试

Python 3.11+：

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -p 'test_*.py' -q
```

测试包含 TLS 登录与流转发、独立服务端初始化、Steam ID 绑定、GSE 安装完整性与备份、协议编码、共享输入、快照和事务存档。公共仓库只保留合成协议向量，不分发真实存档和原生会话捕获；3 项依赖这些私有原始样本的测试会显式跳过。共享房间测试使用匿名的合成配置模板。

## WinForms 启动器

在 Windows 仓库根目录执行：

```powershell
& C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe /nologo /target:winexe /optimize+ /win32manifest:client\launcher.manifest /out:WickedLauncher.exe /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll client\launcher.cs
```

保留 `WickedLauncher.exe.config`，需要 .NET Framework 4.8。`--smoke-test` 短暂打开界面并输出 DPI 诊断。

## Release

服务端和更新包可用构建脚本生成；完整客户端还需要从 Python 官方获得 Windows x64 Python 3.13.2 嵌入式运行环境，并在 Windows 安装 Frida 17.24.3 到独立目录：

```powershell
python -m pip install --target tools\frida-compat frida==17.24.3
python tools\build_public_release.py --runtime <嵌入式Python目录> --frida tools\frida-compat
```

不提供运行环境参数时只构建服务端和客户端更新包。脚本输出分开的 ZIP 和 SHA256SUMS；不会打包 data、transport、数据库或服务器 TLS 私钥。

GSE DLL 保持原始字节；配置模板去除了个人身份和本机路径。不要把游戏主程序、其他来源的破解 DLL 或真实存档放入公共源码目录。
