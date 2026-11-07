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

运行中某名玩家缺少后续输入时，默认最多等待 120 ms，随后由服务端为该玩家统一确认“无新操作”输入，让其他仍在提交输入的玩家继续。补偿帧按 60 Hz 推进，不因未来输入积压而瞬间快进；所有玩家都没有后续输入时不会凭空推进。迟到输入不能重写已确认历史，可靠命令仍按队列消费一次，不复制上一帧的命令或持续按住旧按钮。恢复后的玩家依赖现有 Quantum 确认帧和预测校正；尚需真实双机弱网验收。

可在 `server-config.json` 设置 `input_wait_timeout`（秒，默认 `0.12`），或通过 `WICKED_INPUT_WAIT_TIMEOUT` 覆盖；`0` 关闭缺失输入补偿，恢复等待全部玩家输入的行为。初次激活后尚未提交过新输入的玩家仍至少有 120 秒加载宽限，等待快照的玩家不参与输入等待。正常站立不动仍会提交新的空输入帧，不属于异常。

输入补偿开始和恢复分别记录 `shared-input-fallback`、`shared-input-recovered`。长期没有有效输入进展的玩家仍会被断开，运行中默认阈值为 30 秒；即使其他玩家靠补偿继续推进，该阈值仍生效。通过 `input_stall_timeout` 或 `WICKED_INPUT_STALL_TIMEOUT` 设置阈值，`0` 仅关闭此自动断开。整个房间连续 5 秒没有确认帧进展时，继续限频记录 `shared-input-stall`。异常玩家断开时，涉及他的未完成存档轮次会明确失败，保留已成功提交的存档。

每个实际连接由独立写线程按顺序发送，房间锁内只入队，不等待 TCP 写入；确认帧、可靠命令、快照和心跳共用 FIFO，不合并或丢弃仍在线玩家的确认帧。最旧写入（包括正在阻塞的写入）积压超过 5 秒，或队列超过 8,192 条 / 80 MiB 上限时，记录 `transport-slow-client-disconnected` 并断开该连接，其余玩家继续。心跳时间戳在真正写出时生成，避免把排队时间计为网络往返。

本修复保持客户端模拟、服务端输入排序、既有快照与存档架构；无需额外游戏进程或服务器上的游戏资源。它不能消除差网络玩家自身的延迟、回滚或断线。

## 从源码仓库运行

上文命令对应 Release 中解压后的独立服务端包。源码仓库已按用途整理，维护中的 Python 服务端位于 `server/`，并未归档或停止维护。在仓库根目录执行：

```bash
python -m pip install -r server/requirements.txt
python server/run_private_server.py --init
# 修改根目录生成的 server-config.json 中的加入密码
python server/run_private_server.py --config server-config.json
```

从源码使用 Docker：进入 `deploy/`，将 `server-config.example.json` 复制为 `server-config.json` 并修改密码，再执行 `docker compose up -d --build`。构建上下文会指向仓库根目录，镜像仅复制 `server/`。运行数据和密码配置不进入镜像。

构建独立部署包使用 `python tools/build_server_release.py`。它在 `releases/server-build/` 生成平铺布局的 Server ZIP，解压后仍可直接使用上文原有部署命令。此次目录整理不修改已发布的 v0.2.3 客户端及 v0.1.4 服务端资产。
