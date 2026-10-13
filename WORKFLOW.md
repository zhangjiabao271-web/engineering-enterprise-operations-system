# 开发与验证

从当前请求、相关代码与工作区差异开始；小改动直接实施，跨模块任务再列步骤。只补充能帮助本次决策的上下文。

## 验证范围

| 改动 | 必要验证 |
| --- | --- |
| 文档、提示词、工作规范 | 检查冲突、引用路径和命令；不因纯文字修改重跑业务测试 |
| 文案、样式、局部交互 | 检查受影响代码与实际窗口；运行相关既有测试，不为简单可逆改动机械增加测试 |
| 金额、日期、分摊、状态联动 | 运行相关业务测试；现有覆盖不足时补能复现业务错误的测试 |
| 公共服务、共享组件或跨模块修改 | 运行受影响模块及完整回归；通过后无新变化不重复执行 |
| 数据结构或正式历史数据修复 | 在线备份、隔离副本演练、记录数与金额对账、完整性和外键检查；正式执行应在用户授权范围内 |

界面无法启动时记录具体环境错误；语法检查和业务测试不代替实际窗口验收。发现环境限制时先做独立检查，不以未完成的 UI 验证宣称全链路通过。

## 本地命令

工作目录为仓库根目录。使用项目虚拟环境，不依赖全局 `python`：

```powershell
& '.\.venv\Scripts\python.exe' -m unittest tests.test_finance_dashboard
& '.\.venv\Scripts\python.exe' -m unittest discover -s tests -p 'test_*.py'
& '.\.venv\Scripts\python.exe' scripts\check_database_integrity.py
```

以上分别是发票回款专项、完整回归和正式库只读完整性检查，按任务选择，并非每次依次执行。开源目录使用自身 `CONTRIBUTING.md` 中的 pytest 流程。

## 数据演练与交付

### 服务异常边界

- 每日自检与助手确认录入专项见 `tests/test_operating_attention.py`、`tests/test_assistant_entries.py`；只使用临时合成账本，覆盖缺数、范围、日期冻结、重复确认、取消、账本变化与整批回滚。协助录入不改变查账模式的只读边界。
- 浏览器专项先启动 `scripts/accept_proactive_fixture.py --output qa/全新目录`，再用 Node 运行 `scripts/accept_proactive_workbench.mjs 地址 qa/该目录/browser`。夹具仅用模拟模型，不能冒充真实模型语义验收；真实模型抽查仅使用 `scripts/accept_entry_model.py` 的虚构描述，不发送正式账目。
- 经营主体与业务边界专项见 `tests/test_operating_entity_dashboard.py`、`tests/test_operating_entity_http.py`、`tests/test_business_boundary_guards.py` 及 `tests/test_web_linkages.py`。归属、客户和资金约束由服务验证，不由页面跳过。
- 迁移640只新增管理状态表；已有630账本的演练与正式升级使用 `scripts/upgrade_proactive_workbench.py`，先在线备份和隔离逐表对账，显式 `--apply` 才升级正式库，不清理用户旧备份。

- 页面只处理服务层的 `ValueError` 业务提示，不直接识别 `sqlite3` 异常。
- 服务写入先做明确业务校验；仍可能因并发或数据库约束失败时，仅将可识别的唯一键、外键、检查约束及已知业务触发器错误翻译成面向用户的 `ValueError`。新写入操作可复用 `services/_constraint_errors.py`，提供该操作自己的提示语。
- 未识别的数据库错误与环境故障不伪装成输入错误；界面给通用“状态待核对”提示，同时记录异常。不可在错误后直接建议重复提交。

- 使用 SQLite `Connection.backup()` 创建一致副本；活跃数据库不能只复制主文件，WAL 可能包含已提交数据。
- 将测试数据库和附件目录指向临时位置，且在导入 `db` 或服务之前设置环境变量。测试套件已使用在线备份；部分旧 smoke 脚本仍直接复制主库，运行前须检查并替换其副本创建方式。
- 迁移备份使用 `db.backup.backup_database()`；迁移后核对版本、金额、记录数和外键。导入导出页可备份数据库与引用附件，缺失文件单独报告；恢复时按压缩包说明在隔离目录验证后操作。
- 恢复包覆盖前保全数据库及所有目标附件，`rollback_manifest.json` 记录原件和原本不存在的文件。中途失败一并回滚；自动回滚未完成时停止录入，保留安全材料并明确报告未完成项，不提示直接重复恢复。
- 助手查询范围以项目编号为准，同名项目须澄清；全公司查询清除旧项目编号。已有经营查询登记在 `services/ai_query_capabilities.py`，与聚合工具共用校验和只读快照，不得默默忽略日期、项目或其他条件。新增查询不向模型发送原始账目。
- 助手的实际回款查询 `collections` 包含独立旧账且每笔到账只计一次；`receipts` 仅表达现有项目分配。旧账用独立 `historical_project_id`，不得套用工程项目编号或新增工程收入。逐条排序在截断之前执行；线程只产生结果，切换或清空后的旧结果不得写回会话。
- 网页浏览器入口 `run_web.bat` 和原生 `run_desktop.vbs` 保持正式账本只读；`run_web_demo.bat` 生成隔离合成演示。原生可录入入口为 `run_workbench.vbs`，正式启用状态见 `docs/desktop-workbench.md`。模型授权默认关闭；网页助手会话仅在内存，取消与代次检查必须在结果写回前完成。网页专项见 `tests/test_web_assistant.py`、`tests/test_web_workbench.py`；`scripts/accept_web_iteration.mjs` 只接受可写合成演示，生产模式不用于录入验收。
- 同账本旧 Tk、新 Web 可写入口共用 `db/application_lock.py` 的交互录入锁；不能同时录入。启用写入前保存不覆盖旧文件的数据库及完整附件 ZIP 备份；原生窗口首开最大化，保留可还原尺寸，关闭后释放窗口锁、录入锁和端口。
- 启动/退出专项见 `tests/test_web_desktop.py`，动效与成本弹窗见 `scripts/accept_desktop_shell.mjs`（只接受合成账本）。涉及原生包装时额外实测启动、下载另存为、关闭释放端口及随机端口重启后的视觉偏好；保留用户未保存输入，不直接结束正在使用的进程。
- 切页及工天入口可点击性见 `scripts/accept_web_labor_navigation.mjs 地址 qa/全新目录`，只能连接合成写入验收服务器。真实鼠标验收覆盖往返、页签、翻页、搜索、减少动效以及批量名单最后一人；同一合成服务的复位脚本顺序执行，不能并行重置数据库。
- 网页返回视图状态的无数据库检查见 `scripts/test_workbench_navigation.mjs`（用 Node 运行）；悬浮横向滚动条另在合成长表中实测拖动双向同步、表尾接管、弹窗隐藏和窗口缩放。返回实测包含项目范围、页签、查询、分页及横向位置，不能只核对页面标题。
- 弹窗竞态专项见 `scripts/accept_web_urgent_audit.mjs 地址 qa/全新目录`，仅连接上述合成验收服务器。覆盖延迟响应下的项目/报价切换、关闭后重开、关联查询中禁止提交、材料行移除及保存失败恢复；保存中关闭/Escape和重复提交必须被拦截。`--reproduce` 用于保留修复前失败证据，不代表验收通过。
- 完整写入验收先用 `scripts/accept_web_write_matrix.py --output qa/全新目录` 创建合成账本，再用 `scripts/accept_web_write_matrix.mjs 地址 qa/全新目录/browser` 检查每项落库、取消及重复确认。不能只查看总数或输出末尾；须确认全部 `matrix` 项的 `status` 为 `passed` 且 `errors` 为空。
- 本机工作台稳定性专项见 `tests/test_workbench_stabilization.py`（同期间成本、分摊、主体与资金保护）和 `scripts/test_editor_lifecycle.mjs`（弹窗迟到响应）。成本／主体实际浏览器验收使用 `scripts/accept_workbench_stabilization.mjs 地址 qa/全新输出目录 qa/合成预览.pdf`，只连接上述合成夹具服务；各复位验收脚本顺序执行，不能同时重置同一测试库。
- 正式切换先运行 `scripts/prepare_desktop_cutover.py --output qa/全新隔离目录`，在线备份并把四类引用文件全部重定向到隔离附件目录。恢复演练使用 `scripts/accept_web_backup.mjs`，只接受合成账本或 `qa` 下隔离副本；正式库不做测试记账或恢复。
- 资料语义理解专项见 `tests/test_document_understanding.py`：用合成PDF/文本和模拟模型验收，不为测试上传真实合同或读取真实密钥。网页首次读取明确发送 `use_model:true`，不增加重新识别步骤；人工摘录和旧离线路径不外发。PDF全页送入视觉模型，严格校验完整JSON、页码与证据，金额及累计比例由系统复算。输出与文件、项目/合同/主体版本共同绑定，迟到结果不得重开旧弹窗。正式资料仅经人工核对后关联，转计划仍需独立明确确认。
- 完整 ZIP 恢复先分片上传、版本/文件哈希/完整性/外键与覆盖范围预检，再要求明确输入确认文字。预览后账本或文件变化须重新预览；恢复前保全原数据库和附件，成功后旋转表单令牌并清除旧助手任务，状态不明确时停止写入。专项见 `tests/test_web_backup.py`；只读入口拒绝恢复。
- 切换证据可用 `scripts/verify_desktop_cutover.py` 检查正式账本与隔离基线、下载 ZIP、写入矩阵、窗口模式和已释放端口，输出仅保存到 `qa` 的新文件，不包含会话令牌或模型设置。
- 改动完成后检查本次差异。正式桌面验收需运行新进程验证；重启前保留用户未保存输入，不直接结束正在使用的进程。
- 发布是独立动作，遵循用户已有授权。检查开源目录差异、隐私和 CI；本地测试通过不代表 GitHub CI 已通过。

## 模型更新时

核对官方指南，审计实际加载的规则；保留业务知识、工具使用契约和失败恢复要求，移除重复常识、冲突与无条件流程。用同一组代表性任务比较完成质量、工具调用与耗时，再决定是否继续裁剪；没有测量不声称节省了 token 或提高了性能。
