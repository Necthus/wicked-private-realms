# 当前 Python 服务端

此目录是当前仍维护、与原生客户端配套使用的服务端，不是旧客户端归档。运行入口是 `run_private_server.py`，网络传输实现是 `transport.py`。

在仓库根目录执行：

```bash
python -m pip install -r server/requirements.txt
python server/run_private_server.py --init
# 修改生成的 server-config.json 中的 join_password
python server/run_private_server.py --config server-config.json
```

完整部署教程见 [docs/SERVER.md](../docs/SERVER.md)。构建独立服务端包使用 `python tools/build_server_release.py`。服务器的数据库格式及协议不因源码目录整理而变化。
