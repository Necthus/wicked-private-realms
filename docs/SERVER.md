# 服务端部署

下载 Release 中的 **Server ZIP**。服务端不需要游戏客户端、Steam、GSE 或 SSH 客户端密钥。

## Docker 部署

1. 解压服务端包，进入目录，将 `server-config.example.json` 复制为 `server-config.json`。
2. 修改 `join_password`。Docker 部署保留容器内端口 8443 和 `data_directory=server-data`，以对应预设的端口及卷映射。
3. 执行 `docker compose up -d --build`。
4. 在云安全组和主机防火墙中放行入站 **TCP 8443**。客户端填写 `服务器公网IP:8443`。

默认配置：

```json
{
  "listen": "0.0.0.0",
  "port": 8443,
  "join_password": "替换为你自己的加入密码",
  "data_directory": "server-data"
}
```

Compose 默认把主机 8443 映射到容器 8443；更改主机端口可设置 `REALM_PORT`，容器内配置保持 8443。宿主机存档路径可调整 compose.yaml 的卷映射左侧。包内 `.dockerignore` 排除运行密码配置、存档和 TLS 私钥，重建镜像时保留该文件。查看日志：`docker compose logs -f`。停止：`docker compose down`，不要删除 `server-data`。

## Python 部署

安装 Python 3.11 或更新版本，在解压目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run_private_server.py --init
```

编辑生成的 `server-config.json`，设置加入密码，然后启动：

```bash
.venv/bin/python run_private_server.py --config server-config.json
```

Windows 使用 `.venv\Scripts\python.exe`。不运行 Docker 时，可用包内 `wicked-private-realms.service` 作为 systemd 模板，调整安装路径后启用。

程序自动创建数据库、内部签名密钥和持久 TLS 证书。只有一个公网 TLS 端口；内部 HTTP 和 Photon 服务监听回环地址。首次用户通过密码和 Steam ID 登录即创建账号，其他成员自动可见共享国度。

## 数据与证书

`server-data` 包含世界、角色、历史版本、签名密钥和 TLS 私钥。迁移服务器时完整备份该目录，并保留加入密码配置。服务启动日志会显示 TLS 证书 SHA-256；可提供给玩家核对。

客户端首次连接采用证书指纹首次信任（TOFU），后续证书改变会拒绝连接。首次信任不等同于公开 CA 身份验证，请通过服主提供的服务器地址和指纹核对首次连接。使用自有证书时可在配置中指定 `tls_certificate` 与 `tls_private_key`，客户端仍按指纹固定。

修改加入密码后重启服务。已签发的游戏会话具有有效期，改密码不等于立即撤销所有既有会话。Steam ID 是私服账号索引，当前不验证真实 Steam 账号所有权或调用 Steam Web API 票据验证。

## 健康检查与限制

`https://服务器:8443/health` 返回 `status=ok` 表示 API 可用。自签证书可在命令行使用 `curl -k` 查看；正式客户端会保存并检查指纹。

当前适配构建为 v29466，国度最多 4 人。游戏模拟由客户端执行，服务端负责输入排序、快照、成员和存档。需要同构建及匹配资源；不承诺其他版本可用，也不承诺所有游戏边界条件均已覆盖。
