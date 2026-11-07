# 两台电脑的房主权威测试

当前是开发原型：两台 Windows 电脑各运行一个游戏，房主电脑计算真实世界，客机上传真实键鼠/手柄输入并接收完整权威状态。不需要第三个游戏进程或公网服务器。独立 headless 模拟尚未完成，不能将这个入口部署到 2 核 4 GB 服务器。

## 两台电脑分别准备

1. 使用相同的本分支源码，以及完全一致的 v29466 游戏。入口会核验 EXE、GameAssembly 和 metadata 的 SHA-256；不匹配时拒绝注入。
2. 安装 Python 3.11+，在各自的仓库根目录执行：

```powershell
py -3 -m venv .venv
.venv/Scripts/python.exe -m pip install -r tools/authority_probe/requirements.txt
```

3. 关闭已经运行的游戏。在下方命令中用各自的实际游戏目录替换 `D:\Games\NoRestForTheWicked`。测试工具会自行启动游戏，不通过普通启动器进入。

本入口创建独立测试角色和国度，使用隔离缓存。房主检查点保存在指定状态目录，保留该目录即可在下次房主启动时尝试恢复。它尚不是正式多人存档系统；请先使用新测试国度验收。

## 房主电脑

```powershell
.venv/Scripts/python.exe tools/play_authority_lan.py host `
  --game-directory "D:\Games\NoRestForTheWicked" `
  --listen 0.0.0.0 --port 18881 `
  --state-directory dev-artifacts/lan-host
```

保持终端开启。出现 `lan-listening` 表示 TCP 入口启动；出现 `stream-view-ready` 表示房主角色视图绑定完成。通过 `ipconfig` 查到房主当前网卡的局域网 IPv4 地址，将地址与 `dev-artifacts/lan-host/join-code.txt` 的内容单独发给同学。只需分享加入码，不要分享整个状态目录。

房主 Windows 防火墙需允许此 Python 的 TCP 18881 入站。如果连接被阻止，可在管理员 PowerShell 中显式添加局域网规则；规则不是工具自动创建的：

```powershell
New-NetFirewallRule -DisplayName "Wicked authority LAN test" `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 18881 `
  -Program (Resolve-Path .venv/Scripts/python.exe).Path -RemoteAddress LocalSubnet
```

## 客机电脑

先检查网络连通性，用实际房主地址替换示例：

```powershell
Test-NetConnection 192.168.1.20 -Port 18881
.venv/Scripts/python.exe tools/play_authority_lan.py guest `
  --game-directory "D:\Games\NoRestForTheWicked" `
  --host 192.168.1.20 --port 18881 `
  --state-directory dev-artifacts/lan-guest
```

在终端提示 `房主的加入码:` 时粘贴加入码，按回车；输入不回显。也可将码存为本机文本文件，通过 `--code-file <本机文件路径>` 读取。

出现 `lan-connected` 后等待 `receiver-view-ready`，然后将焦点切到各自游戏窗口。房主拥有槽位 0，客机拥有槽位 1；默认使用真实输入。`--synthetic-movement` 和 `--allow-local-diagnostic` 仅用于开发诊断，双机人工测试不使用。

## 人工验收和退出

依次记录以下结果；任意一项失败，保留两端日志再继续排查：

- 两人分别移动、停止、转向，能互相看到对方，操作不会控制错角色。
- 分别进行攻击、闪避、受伤和拾取，检查敌人、血量和物品结果是否一致。
- 操作场景交互、死亡重生和场景切换，观察是否卡住、回跳或产生重复物品。
- 客机退出后，房主仍能移动；重新启动客机命令，检查是否恢复到原来的客机角色。此重连路径尚待双机人工验收。
- 两端正常退出后，用相同房主状态目录重启，再加入，检查世界和角色是否恢复。

在各自终端按 Ctrl+C 结束测试，会关闭工具创建的游戏进程并写出 `result.json`。每次运行的记录位于状态目录下的 `runs/<角色-时间-随机码>/`，包含 `events.jsonl` 和 `game.log`。报告中的 `states_verified` 表示完整帧校验次数，`inputs_received` 表示房主收到的客机输入数；这些计数不能替代上述人工游玩验收。`physical_two_pc_verified` 和 `manual_gameplay_verified` 保持 false，因为工具不能自行确认测试机器和人工体验。

本协议用于可信局域网，尚未加入 TLS、正式账号绑定或公网部署支持。完整状态传输的带宽和画面平滑仍需实测。当前版本不能据回环成功宣称战斗、交互或双机游玩已验收。

测试后若不再需要防火墙规则，可在管理员 PowerShell 中移除：

```powershell
Remove-NetFirewallRule -DisplayName "Wicked authority LAN test"
```
