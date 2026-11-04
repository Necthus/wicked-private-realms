服务端、客户端已分开发布。此版本为 v29466 实验性预发布。

### 服务端

下载 **Wicked-Private-Realms-Server-v0.1.0.zip**。配置加入密码、监听端口和数据目录后，通过 Docker 或 Python 启动；自动创建数据库与 TLS 证书，不需要游戏文件或 Steam。

教程：https://github.com/Necthus/wicked-private-realms/blob/main/docs/SERVER.md

### 客户端

新玩家下载 **Wicked-Private-Realms-Client-v29466-v0.1.0.zip**，已有运行环境可下载 Client-Update。把 wicked-client 放到游戏 EXE 旁，启动后填写服务器地址、密码和自己的 SteamID64，直接经 TLS 连接。无需 SSH 私钥。

包含独立 WinForms 启动器、DPI 修复、Frida 17.24.3，以及既有已测试 GSE DLL 和全部 8 个 steam_settings 文件。默认启用随包 GSE，可为其他模拟版安装统一兼容层；替换前备份原 DLL/设置。GSE DLL SHA-256 为 723e13f5a2e1ec799f342612a3294229ca832d143fdea85c00025216b6696e88，发布模板已去个人配置。

教程：https://github.com/Necthus/wicked-private-realms/blob/main/docs/CLIENT.md

### 验证与范围

本地完整测试 89 项通过。公共源码 89 项通过并显式跳过 3 项未分发的私有原始捕获测试。新增覆盖：密码拒绝、证书变化拒绝、1 MiB 二进制流转发、全新服务端只靠配置启动、真实 Photon 初始化、GSE 安装与备份。

公网 TLS 登录、游戏 API 以及实际游戏主菜单加载已验证，保留角色/国度及图形设置正常。既有传输曾通过双客户端互动；此新 TLS Release 的完整双机游戏流程仍需回归。仅支持已记录的 v29466 构建，不承诺其他版本或任意模组可用。

首次连接采用证书指纹首次信任，后续固定；Steam ID 为私服索引，不验证真实 Steam 所有权。备份服务端数据目录和证书。下载后可用 SHA256SUMS.txt 校验。
