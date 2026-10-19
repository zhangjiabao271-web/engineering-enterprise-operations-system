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

- 本机主动服务专项：`tests/test_operating_attention.py`、`tests/test_assistant_entries.py`，覆盖自检人工确认、跨日、变化重开、忽略恢复、草稿缺数、范围、版本、整笔回滚和重复确认。`db.connection.use_write_transaction` 仅供已开启的独立事务组合原业务服务；不能跨库或由只读快照进入写入。
- 实际页面验收先运行 `scripts/accept_proactive_fixture.py --output qa/全新目录`，再运行 `scripts/accept_proactive_workbench.mjs 地址 qa/该目录/browser`。只使用合成账本和模拟模型，不能冒充真实模型语义验收。迁移640只增加核对、跟进与草稿状态；正式启用前在线备份、副本升级、历史事实逐表对账及外键检查，不在打开的正式窗口中迁移。

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
- 同账本的 Web 写入窗口使用 `db/application_lock.py` 的交互录入锁，不能重复录入。旧 Tk 界面已退役；启用写入前仍保存不覆盖旧文件的数据库及完整附件 ZIP 备份，关闭窗口后释放窗口锁、录入锁和端口。
- 启动/退出专项见 `tests/test_web_desktop.py`，动效与成本弹窗见 `scripts/accept_desktop_shell.mjs`（只接受合成账本）。涉及原生包装时额外实测启动、下载另存为、关闭释放端口及随机端口重启后的视觉偏好；保留用户未保存输入，不直接结束正在使用的进程。
- 切页及工天入口可点击性见 `scripts/accept_web_labor_navigation.mjs 地址 qa/全新目录`，只能连接合成写入验收服务器。真实鼠标验收覆盖往返、页签、翻页、搜索、减少动效以及批量名单最后一人；同一合成服务的复位脚本顺序执行，不能并行重置数据库。
- 网页返回视图状态的无数据库检查见 `scripts/test_workbench_navigation.mjs`（用 Node 运行）；悬浮横向滚动条另在合成长表中实测拖动双向同步、表尾接管、弹窗隐藏和窗口缩放。返回实测包含项目范围、页签、查询、分页及横向位置，不能只核对页面标题。
- 弹窗竞态专项见 `scripts/accept_web_urgent_audit.mjs 地址 qa/全新目录`，仅连接上述合成验收服务器。覆盖延迟响应下的项目/报价切换、关闭后重开、关联查询中禁止提交、材料行移除及保存失败恢复；保存中关闭/Escape和重复提交必须被拦截。`--reproduce` 用于保留修复前失败证据，不代表验收通过。
- 完整写入验收先用 `scripts/accept_web_write_matrix.py --output qa/全新目录` 创建合成账本，再用 `scripts/accept_web_write_matrix.mjs 地址 qa/全新目录/browser` 检查每项落库、取消及重复确认。不能只查看总数或输出末尾；须确认全部 `matrix` 项的 `status` 为 `passed` 且 `errors` 为空。
- 本机工作台稳定性专项见 `tests/test_workbench_stabilization.py`（同期间成本、分摊、主体与资金保护）和 `scripts/test_editor_lifecycle.mjs`（弹窗迟到响应）。成本／主体实际浏览器验收使用 `scripts/accept_workbench_stabilization.mjs 地址 qa/全新输出目录 qa/合成预览.pdf`，只连接上述合成夹具服务；各复位验收脚本顺序执行，不能同时重置同一测试库。
- 经营主体合同继承与看板专项见 `tests/test_operating_entity_dashboard.py`、`tests/test_operating_entity_http.py`。实际浏览器验收先用 `scripts/accept_operating_entity_fixture.py --output qa/全新目录` 生成合成账本，再运行 `scripts/accept_operating_entities.mjs 地址 qa/该目录/browser`；覆盖确认、取消、状态刷新、明细对账、批量、迟到响应与手机布局。只连接该合成服务，不批量确认正式历史主体归属。
- 正式切换先运行 `scripts/prepare_desktop_cutover.py --output qa/全新隔离目录`，在线备份并把四类引用文件全部重定向到隔离附件目录。恢复演练使用 `scripts/accept_web_backup.mjs`，只接受合成账本或 `qa` 下隔离副本；正式库不做测试记账或恢复。
- 资料语义理解专项见 `tests/test_document_understanding.py`：用合成PDF/文本和模拟模型验收，不为测试上传真实合同或读取真实密钥。网页首次读取明确发送 `use_model:true`，不增加重新识别步骤；人工摘录和旧离线路径不外发。PDF全页送入视觉模型，严格校验完整JSON、页码与证据，金额及累计比例由系统复算。输出与文件、项目/合同/主体版本共同绑定，迟到结果不得重开旧弹窗。正式资料仅经人工核对后关联，转计划仍需独立明确确认。
- 完整 ZIP 恢复先分片上传、版本/文件哈希/完整性/外键与覆盖范围预检，再要求明确输入确认文字。预览后账本或文件变化须重新预览；恢复前保全原数据库和附件，成功后旋转表单令牌并清除旧助手任务，状态不明确时停止写入。专项见 `tests/test_web_backup.py`；只读入口拒绝恢复。
- 切换证据可用 `scripts/verify_desktop_cutover.py` 检查正式账本与隔离基线、下载 ZIP、写入矩阵、窗口模式和已释放端口，输出仅保存到 `qa` 的新文件，不包含会话令牌或模型设置。
- 改动完成后检查本次差异。正式桌面验收需运行新进程验证；重启前保留用户未保存输入，不直接结束正在使用的进程。
- 网页录入级联专项见 `tests/test_web_linkages.py` 和 `scripts/accept_web_linkages.mjs 地址 qa/全新输出目录`。浏览器仅连 `accept_web_write_matrix.py` 合成服务，覆盖主体继承后的余额、项目／合同／收入／发票候选、PDF表单、回款重新分配、单人／批量地点及年度合同默认值；PDF识别响应使用合成结果，只验证本轮表单联动，不冒充真实PDF识别验收。
- 历史归属边界专项见 `tests/test_business_boundary_guards.py`；覆盖主体继承对资金／进项票的保护、补充协议上级、未知／冲突、批量回滚、客户一致性及回款归属锁定。`scripts/accept_business_boundaries.mjs 地址 qa/全新输出目录` 只连接上述合成服务，检查真实弹窗错误、保留输入、正常更正、取消不写、锁定搜索框及手机说明。补充协议上级更换后派生发票抵扣刷新由 `tests/test_web_linkages.py` 验证，不改实际到账或项目收入。
- 发布是独立动作，遵循用户已有授权。检查开源目录差异、隐私和 CI；本地测试通过不代表 GitHub CI 已通过。

- 回款补足收入更正专项：`tests/test_receipt_income_corrections.py` 与 `scripts/test_receipt_income.mjs`；合成浏览器先运行 `scripts/accept_receipt_correction_fixture.py --output qa/全新目录`，再运行 `scripts/accept_receipt_corrections.mjs 地址 qa/该目录/browser`。验证明确选择、取消、关联保护和同事务追溯，不向正式账本提交更正测试。
- 新账本初始化与隐私专项：`tests/test_initial_setup_privacy.py`。已有账本不重新初始化；历史迁移保持原样。在发布仓库根目录运行 `python scripts/check_release_privacy.py`；维护者可用 `--private-ledger` 指定本机账本做只读主体名称比对。正式数据不进入发布包。

合同客户分组专项：`tests/test_contract_ledger.py` 覆盖签约年份与全周期累计、客户／主体／项目范围、有效分配过滤、多合同分摊及逐笔对账。先运行 `scripts/accept_contract_ledger_fixture.py --output qa/全新目录`，再运行 `scripts/accept_contract_ledger.mjs 地址 qa/该目录`；只连接生成的合成账本，验收客户翻页返回、金额明细、附件预览和计数刷新、取消不写及窄屏布局。导航状态另运行 `scripts/test_workbench_navigation.mjs`。

现金与未签业务汇总专项：`tests/test_contract_cash_business.py` 和合同台账HTTP测试覆盖全业务/仅合同/未关联范围、约定不当收入、预收不抵其他项目、部分发票归属守恒、拆分回款、主体冲突、作废状态及只读对账。用 `scripts/accept_contract_cash_fixture.py --output qa/全新目录` 创建合成账本，再运行 `node scripts/accept_contract_cash.mjs 地址 qa/该目录` 验收四卡懒加载明细、客户下原记录、范围切换、主体/搜索和窄屏。只调整查询与展示，不批量补合同关联、不重建收入回款、不增加迁移。

项目业务主线专项：`tests/test_project_business_flow.py` 覆盖自动合同关系、未签业务、跨合同顺序抵扣、补录重排、明确指定、预收、跨项目／主体隔离、补签历史与事务回滚。界面先运行 `scripts/accept_project_business_fixture.py --output qa/全新目录`，再运行 `node scripts/accept_project_business.mjs 地址 qa/该目录`；只连合成服务。迁移650只增加规则与追溯结构，旧回款默认保留指定范围；正式启用前在线备份、隔离升级、原事实逐表对账，并在交互录入锁释放后执行。

采购耗材均摊专项：`tests/test_purchase_consumable_allocations.py` 覆盖整单含运费守恒、混合材料类别、修改／取消归集、版本历史和成本明细。实际浏览器验收先用 `scripts/accept_purchase_supplier_fixture.py --output qa/全新目录` 启动合成服务，再运行 `node scripts/accept_purchase_consumables.mjs 地址 qa/该目录/browser`；覆盖默认未完工范围、勾选、尾差、迟到预览、连续录入和历史项目保留。只连合成账本，不重分摊正式历史采购。

工作区收拢专项：`tests/test_project_accounting.py`、`tests/test_partner_overview.py`、`tests/test_workbench_information_architecture.py`，覆盖账面与已确认毛利分层、全部成本守恒、客户业务年度与合同签约年区别、有效分配及客户/供应商精确范围；运行 `node scripts/test_workbench_navigation.mjs` 检查旧链接与返回状态。界面验收仅使用 `scripts/accept_workspace_fixture.py --output qa/全新目录` 合成账本及 `node scripts/accept_workspace_consolidation.mjs 地址 qa/该目录`，不以测试录入修改正式账本。

## Web 单界面与旧 Tk 退役验证

- 旧 `main.py`、`run.bat`、`pages/`、`ui/` 及专用界面测试已移除。正式入口是 `run_workbench.vbs`；`run_desktop.vbs` 是同一 WebView2 的只读入口，不是旧 Tk 程序。
- Excel 专项运行 `tests.test_excel_transfer_service`、`tests.test_tk_retirement` 与现有 Web Excel 写入测试，覆盖预览不写、整批回滚、重复与过期拒绝、含税及运费守恒、公式安全和四类导出。金额、数据库和服务层测试继续保留，不能只靠删除测试获得通过。
- 在未安装 ttkbootstrap 的全新环境安装当前 `requirements.txt` 与 `requirements-desktop.txt`，再验收启动、批量工天、采购、合同回款、附件、Excel 和备份恢复；新代码不得再依赖旧页面。回归测试用阻断导入进一步覆盖 tkinter、旧页面和旧组件依赖。
- 退役只删除源码与生成缓存，不卸载用户现有环境或触碰正式业务数据。恢复材料和范围见 `docs/legacy-tk-retirement.md`；不会自动同步或发布独立开源仓库。

## 模型更新时

核对官方指南，审计实际加载的规则；保留业务知识、工具使用契约和失败恢复要求，移除重复常识、冲突与无条件流程。用同一组代表性任务比较完成质量、工具调用与耗时，再决定是否继续裁剪；没有测量不声称节省了 token 或提高了性能。
