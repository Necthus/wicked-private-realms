# 序章原流程恢复（v0.2.4）

此前原生宿主固定发送 flags=1，适配器据此把每一次 Session.Join 的 prologue 参数强制改为 false。
这是诊断共享国度入场问题时遗留的行为，也会影响纯本地国度，不符合保留游戏原本流程的目标。

当前修正：

- `JoinHook` 原样转发游戏提供的 prologue、respawn 以及其他全部参数，不再覆盖序章决定。
- 宿主不再启用跳过序章标志。v1 共享内存布局保持兼容，DLL 忽略旧宿主可能发送的历史标志。
- 本地和共享国度均由游戏原流程决定序章资格；共享国度的成员、保存及网络适配逻辑未改变。
- 不重置已有角色、世界或账号的教程完成状态，也不强制为已完成序章的角色重播。

DLL 自检增加入场参数转发回归：覆盖旧标志 0/1、序章 0/1、重生 0/1，逐一检查全部六个参数且只调用原函数一次。
序章检查对应新增的 0x20 位；加入本地国度隔离检查后，完整成功掩码为 0x7F。

v0.2.4 已恢复参数转发，并通过 DLL 回归检查；本地国度读取、启动和保存流程已验证。具体序章资格仍由原游戏决定，不据此承诺所有角色状态下的船上流程均已实测。
仅新建国度并不保证游戏会再次请求序章，应以游戏本身决定和 `session-join-request` 日志为准。

## v29466 原版共享国度的序章分支

已从未修改的 GameAssembly.dll 核对：

- `ClientFlowAPI.ProceedToSession`（RVA 0x890DC10）在共享国度分支中读取 `SessionRequestData.PlayPrologue`（偏移 0x40）。为 true 时，保留请求到 `ScheduledSharedSessionRequest`，调用 `SessionDefinition.CreatePrologueRealm`（调用点 RVA 0x890E100）先启动独立序章会话；为 false 时继续请求共享国度后端。
- `ClientFlowAPI.TryConsumeSharedSessionJoinRequest`（RVA 0x890D5A0）后续取出排队的共享请求，将其 `PlayPrologue` 清为 false（RVA 0x890D6B5），避免再次启动序章。
- 因此，原版有阶段明确的“不再播放序章”处理，也允许共享国度入场请求先经过独立序章。补丁应保留原版这两个阶段的决定，不应在最初请求时一律覆盖为 false。

这些代码不能单独证明上层界面何时判定序章资格；账号、角色和教程完成状态的具体规则仍以原游戏流程为准。
