# v29466 五人联机可行性核对

状态：已实现独立的五人实验客户端与服务端。一个真实客户端强制占用第五模拟槽位，已验证入场、合成输入移动、退出保存、帧复制、快照读写和校验一致；五个真实客户端同时游玩尚未验收。当前生产部署继续保持四人限制。

## 已确认的限制

| 层级 | 当前实现 | 影响 |
|---|---|---|
| 私服房间 | `server/transport.py` 拒绝第五人，并返回 MaxPlayers=4 | 可修改的服务端限制 |
| 私服时间线 | `ConfirmedRoom` 要求 PlayerCount=4，分配四个槽并输出四槽确认帧 | 可参数化，但所有客户端必须一致 |
| Quantum 会话 | 当前真实客户端请求 PlayerCount=4 | 需在客户端创建模拟前统一配置 |
| 游戏全局输入 | `_globals_._input_` 是四项 Input 固定数组 | 已编译的内存结构限制 |
| 游戏组件 | TriggerComponent、CinematicComponent 等含按玩家存储的四项数组 | 需要逐项核对访问与序列化 |

`server/server.py` 的旧控制 API 也有四人 presence 限制；它不能代替 Photon/Quantum 实际同步路径的扩容。

## 原始客户端二进制证据

已对未修改的 v29466 GameAssembly.dll 进行只读核对：

- `Quantum.Input.SIZE` 为 272（0x110）。
- `_globals_` 大小为 2208；输入数组位于偏移 0x260，共四项，占 1088（0x440）字节。
- 紧随输入数组的 `PlayerLastConnectionState` 位于 0x6A0。
- `_globals_.get_input` 的 RVA 为 0x5EDE230；其中 RVA 0x5EDE274 写入元素步长 0x110，RVA 0x5EDE27B 写入固定长度 4。
- 因此，若仅解除边界检查并按原数组布局写入第五项，起点是 `0x260 + 4 * 0x110 = 0x6A0`，会覆盖其他模拟状态。若保留边界检查，则可能直接拒绝第五项。
- `TriggerComponent.IsTriggeredByPlayer` 为四项 QBoolean；`CinematicComponent` 的 ReturnAfterCinematic、ReturnPosition、ReturnRotation 也是四项固定数组。这些结构有各自的生成序列化函数和固定字段偏移。

以上是具体组件证据，并不意味着每个长度为四的数组都与人数有关；装备、宝石等数组不能批量替换。

## 已做的协议检查

现有 `quantum_wire.input_frame` 支持可变玩家数量。使用五个不同的 47 字节输入构造确认帧，并回读帧头、五位玩家掩码、五个输入及标志，均一致。该检查仅说明编码层可表达五人，不证明原游戏能分配或模拟第五个玩家。

## 可尝试的实现路线

1. 在独立构建和隔离国度中统一 Photon 人数、Quantum PlayerCount 和私服槽位分配。
2. 审计客户端所有按玩家索引的结构及读写路径，选择完整结构扩容或可随模拟帧回滚的扩展存储。仅在进程外加一个普通数组不足以保证回滚、快照和校验一致。
3. 同步处理分配、复制、哈希、快照序列化与反序列化，以及关联组件；第五位玩家应先通过创建实体、输入和确定性检查。
4. 五个使用相同实验补丁的真实客户端验证入场、移动、战斗、过场、地图切换、保存、退出及重新加入。合成连接不能替代第五个游戏客户端。

如果能够获得匹配游戏项目的 Quantum 生成代码及可重新构建的模拟工程，结构扩容会更直接。目前只有编译后的客户端，需要进行较大范围的二进制适配，不能将其作为单一配置项承诺可用。

本轮没有改变生产服务器人数、已安装客户端或公开 Release。

## 当前实验实现

源码仅在 `experimental/five-player` 分支；正式 `main` 与正常构建仍为四人版。

- `server/transport.py` 可配置 4 或 5，默认 4；`ConfirmedRoom` 按容量分配槽位、确认输入并保持配置一致。
- `native/adapter/five_player_experiment.h` 只在 `--experimental-five` 构建中编译，并且仍需宿主显式启用。扩展每帧全局内存末尾的输入空间，保留原字段偏移，更新分配、清零、复制和序列化。
- `native/adapter/five_player_overlay.h` 在每帧末尾保存第五人的组件数据，以实体和组件字段标识；修改已核对的玩家状态边界判断，避免写入原四项数组后续字段。过场状态、过场时钟、触发器字段的扩展参与快照及校验。
- 扩展位置解析数组、连接状态和五槽保存容器；本地四槽会话的保存容器补齐一个空槽，但不改动本地会话玩家配置。
- 增加真实机器码跳转与原地存储回归，确认 0～3 原槽保持原值、4 使用扩展字段、越界索引仍走原拒绝路径。

开发构建：

```powershell
python native/build_native.py --experimental-five
python native/build_host.py --experimental-five
python native/build_selftest.py --experimental-five
python -m unittest tests.test_five_player_experiment -q
```

编译输出位于忽略的 `native/dist-five/` 和 `native/build-five/`，不会覆盖普通 `native/dist/`。宿主需环境变量 `WICKED_EXPERIMENTAL_PLAYERS=5`；测试服务器设置 `max_players=5`。`experimental_first_slot=4` 仅用于单客户端直接检测第五槽位，正常五人房间保持 0。

实验标记不会自动开启生产服务器。源码仓库不提交 EXE/DLL、真实存档或探针日志。

## 实测边界与剩余验收

已完成的单客户端测试使用独立数据库及游戏原有入场/返回菜单流程；开发探针只提供合成输入、读取角色位置和对临时帧执行快照往返。未以 Frida 替代原生第五槽位适配。用户存档在每轮结束后从备份恢复。

第五槽位的合成输入已使角色位置发生变化；在线保存周期取得成功确认，隔离数据库增加了世界版本。对一个临时复制帧写入非零第五人字段后，校验值发生变化；完整序列化/反序列化后的校验值相同，证明所测扩展字段并未遗漏于快照。

仍需五个真实客户端同时验证：五路真实键鼠输入、不同角色存档、战斗与互动、迟加入的真实网络快照、长距离地图切换、复杂过场、断线重连及长期运行。当前测试不证明这些全部可用。

扩展字段表为每帧固定 4096 项，满时会报告明确错误并停止，避免静默覆盖；所有按玩家索引的编译路径仍需随实测继续审计。不要用实验服务器打开唯一一份生产国度数据库。

## 打包

使用已发布的正常原生客户端包作为 GSE 与静态资源来源：

```powershell
python native/build_five_player_release.py --stable-client <v0.2.5客户端ZIP>
```

脚本输出独立的实验 Client/Server ZIP 与 SHA256SUMS，不上传、不部署。客户端包仍不携带 Python、Frida 或源码。测试使用 [专用入口和部署说明](FIVE-PLAYER-TEST.md)。
