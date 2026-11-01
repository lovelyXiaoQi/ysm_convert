# MC 3.10 玩家换装贴图兼容处理

开发包 `3.10.0.436581` 与 `3.10.0.448891` 的 Python 层比较发现，
`ActorRenderCompClient.ResetEntityExtraSkin` 的底层调用由
`entity_module.reset_entity_render` 改为 `reset_entity_extra_skin`，同时调整了操作历史清理顺序。
用户反馈更新后所有模型在切换时贴图错位/串图；尚未通过实机确认具体的底层缓存故障。

此分支把本地 T2 测试包的候选兼容处理移到源码，供 MC Studio 正常编译发布：

- 仅通过公开的 `ResetEntityExtraSkin` 重置玩家渲染，不导入引擎内部模块、
  不读写操作历史，也不替换引擎组件类。
- 比 `3.10.0.436581` 新的引擎（`clientApi.GetEngineVersion()`，认不出版本号按新版处理）切换模型时
  先完整重置、重建一次，再延迟 0.05 秒重新绑定模型资源；更早的引擎照旧同一帧做完 ——
  分帧会先露出一帧原版皮肤，没有这个问题的版本不该承受。
  普通的非重置重建仍同步执行；若它接替尚未完成的重置，则继承延迟与 `renderReset=True`。
- 快速切换只让最新请求生效。恢复原版皮肤、离开视野或引擎重新创建玩家时取消旧回调。
  排程开始即清掉旧模型的判重指纹，避免 `A -> B -> A` 把最后一次 A 判成重复数据。
- 贴图注册、渲染重建、重置的返回值和 `GetActorRenderParams` 的逻辑贴图表**只记诊断日志**
  （前缀 `[YSM][RenderCompat]`），不当成败判据：误判会让 `PlayerModelAppliedClientEvent`
  发不出去（其他模组靠它重挂资源）、整套重做并再闪一次。事件照旧在完整资源应用与重建之后发出，
  保留原原因和重置标记。应用过程抛出的异常不吞，照常交给引擎记录；计时器排不上时当场应用。

T1 测试包曾因外部字节码工具把 `PRINT_ITEM` 编成引擎不支持的别名，
在日志函数启动调用时触发 `unknown opcode`，导致客户端与换装菜单未加载。
源码分支不包含该手工字节码打包器；日志使用受异常保护的普通 `print`，模块加载时不执行引擎调用。
发布检查还确认 `entity_module`、`client.render.operationHistory` 无法导入，`sys` 输出被禁用，
因此源码已移除 T2 里的旧 native 重置尝试和 `sys.stdout` 依赖。

## 验证

2026-10-31 使用 [MCNeteaseDevs/modsdk_mcp_server](https://github.com/MCNeteaseDevs/modsdk_mcp_server)
的 `8eb80e3306144fb0246459c86e14f550eff1a7a8` 快照，通过 stdio MCP 实际调用
`get_development_guidance`、`get_api_detail`、`get_document_section` 和 `review_code`。
该服务的 `current` 是 ModSDK 3.9 / BE 1.21.120；请求 3.10 会返回未知版本，不能作为 3.10 运行验证。

- 旧提交的兼容模块检出三项 `PY_IMPORT_NOT_WHITELISTED`，对应 `sys`、`entity_module`、
  `client.render.operationHistory`；移除后该模块为零问题。
- 同时复查 `playerRender.py`、`modelSystem.py`，没有 critical，保留三个原有的循环内日志 warning。
  这些日志不在本次修改范围，不能把报告表述为整个项目完全无警告或发布审核已通过。
- 文档确认 `ResetEntityExtraSkin` 后须手动重建，玩家的操作历史由公开接口内部清理；
  `AddPlayerTexture` 和 `RebuildPlayerRender` 返回布尔值，`AddTimer` 支持传递回调参数。
  本机两版 3.10 SDK 的相关 Python 包装层比较结果另用于核对版本差异。

离线回归运行 `python devtools/test_player_render_compat.py`（Python 2.7）。
23 项通过，使用实际的 `_ApplyPlayerModelNow`、官方皮肤恢复和 AOI 离开处理函数，配合假引擎验证
按引擎版本分帧、取消、回退、返回值只记日志不拦事件、异常不吞、判重指纹与扩展事件时序。
`test_pack_parser` 679 项、扩展接口 34 项、材质/PBR 68 项也通过。

2026-10-31 在 `3.10.0.436581`（本机最新，仍是改动之前的版本）实机验证，自检
`mcdkSelfTest.mcdk_test_render_compat_switch_start()` / `_result()` 走真实链路（`ReplaceYsmModel`）换模型：

- `GetEngineVersion()` 返回 `'3.10.0.436581'`，按版本判定不分帧。
- `GetActorRenderParams(id, "textures")` 返回键列表，带 `True` 返回同顺序的值列表，`ysm` 键的值与模型数据的
  `texture` 逐字一致（不带后缀）。
- 强制分帧（`force=True`，即新版引擎走的路径）与按版本同帧各跑一轮 `A -> B -> A` 再换回原模型；旧版写法模型
  （`default` 贴图键）与 JSON 模型来回切也跑了一轮：每一步都应用完成、贴图键对得上、正好一条
  `("change", renderReset=True)` 事件，提交到应用完 59～394 ms。
- 强制分帧下同一帧连续应用三个模型，只有最后一个生效、只发一条事件；等待绑定期间卸模型，待绑定的请求被取消。
- 登录时第一次应用（玩家渲染还没就绪）`AddPlayerTexture` 与 `RebuildPlayerRender` 都返回 False、贴图表读不到，
  稍后玩家实体建好再应用全部成功 —— 这是把返回值当成败判据会误判的实例，现在只记日志。

测试主包使用仓库的 `python scripts/pack_scripts.py --keep-print` 生成 `publish/`，
保留普通诊断日志，不使用旧的手工字节码注入工具，也不递增正式包版本。

串图本身还没复现：本机没有 `3.10.0.448891`。在该版本上要检查菜单打开、`A -> B -> A`、同模型换肤、快速切换、
恢复原版/官方 4D 皮肤以及第一/第三人称效果；先跑 `mcdk_test_render_compat_switch_start(force=None)`
（按版本应走分帧）取 `_result()`，再看 `[YSM][RenderCompat]` 日志里 `bound` 与 `expected` 是否一致。
离线测试不等同于引擎运行验证。
