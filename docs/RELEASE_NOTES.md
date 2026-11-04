v0.1.4 修复多人聊天中“自己发的消息显示两次、对方只收到一次”。

原因：原版聊天默认使用 Photon ReceiverGroup.Others，发送者本地已显示一次；私服原先向包括发送者在内的所有玩家转发，导致本地多了一份回送。现在按接收者范围转发，缺省排除发送者，保留 All、MasterClient 和指定目标语义。不会按文字内容去重，因此主动重复发送仍正常。消息内容和发件人标识保持原样。

这是服务端修复。已有 v0.1.3 客户端无需重新下载；服主更新源码或 Server ZIP 后重启服务，保留 server-config.json 与 server-data。

新增 7 项聊天包接收测试，覆盖双向发送、主动重复、接收组、指定目标以及某个接收者离开；完整检查共 99 项，公开源码中 3 项私有原始样本测试跳过。实际双机聊天 UI 回归将在部署后由玩家操作验证。

本次仅发布服务端修复包。客户端继续使用 v0.1.3：https://github.com/Necthus/wicked-private-realms/releases/tag/v0.1.3 ，包含已修复的便携 Python 导入路径、Frida 17.24.3、指定 GSE DLL 与全部设置。Steam ID、角色和世界数据不受本次修复影响。

服务端教程：https://github.com/Necthus/wicked-private-realms/blob/main/docs/SERVER.md

客户端教程：https://github.com/Necthus/wicked-private-realms/blob/main/docs/CLIENT.md
