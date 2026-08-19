# 工程企业经营系统数据库架构（V3 数据底座 / V4 演进说明）

更新日期：2026-07-30

实施状态：V3 阶段 0/1 数据底座已落地。迁移版本 `100` 至 `150` 已应用；客商、材料报价、项目、采购和项目利润页面已切换到 V3 service。采购单以 `supplier_partner_id`、`material_id`、`supplier_offer_id` 为权威关联，旧 ID 仅用于历史导入追溯。版本 `140` 增加供应商默认税率，以及采购时的未税价、税率、税额、含税材料额和运费快照；版本 `150` 修复施工记录自动地点与 V3 项目地点的同步完整性。

V4 正式经营事实已落地，新增迁移版本 `160` 至 `200`：

- `160`：合同、项目分配与结算。
- `170`：销项发票、回款与项目核销。
- `180`：成本台账（付款事实功能已移除，`payment_entries` 保留为空表）。
- `190`：工天项目归属、UUID 和金额快照。
- `200`：补充协议上级合同关联和经营事实附件。

经营驾驶舱、项目工作空间、合同结算、开票回款、成本页面均通过服务层读取正式事实。V4 的目标模型和模块边界以 `ARCHITECTURE_V4.md` 为准；本文件继续保留 V3 历史决策。

当前产品北极星为“项目经营可核算率”：同时存在有效合同项目分配和有效结算确认的在营项目数，除以全部在营项目数。

## 1. 系统定位

本系统不再以“供应商管理”为边界，而以工程企业从商机、项目立项、合同履约、采购施工、成本归集到开票回款的经营闭环为边界。

核心原则：

- **项目是主要经营维度，但不是所有业务的强制归属。** 框架采购、库存采购、总部费用可以暂不关联项目，之后通过分摊单归集到一个或多个项目。
- **客商统一建档。** 同一法人可同时是客户、供应商、分包商或其他合作方，角色不应决定其主数据身份。
- **业务事实与财务事实分开。** 订单、验收、发票、收付款、成本入账分别记录，再通过明确的核销和分摊关系连接。
- **历史单据保存快照。** 名称、规格、价格、税率和交易对手信息在过账或审批时固化，主数据后续修改不得改写历史。
- **单据可追溯，不物理删除。** 已审批、已履约、已结算、已核销的单据只能作废、红冲或生成调整单。
- 当前可继续使用 SQLite 支撑单机版，但结构和数据类型必须兼容未来迁移到 PostgreSQL。

## 2. 当前数据库审计结论

2026-07-19 实库共有：供应商 8、产品 28、旧采购 13、新采购单 21、项目 8、工人 4、工天 16、施工记录 5、施工地点 3。

已落地的正确方向：

- 已启用 `schema_migrations`，并完成两次数据迁移记录。
- 已建立 `projects`、采购单头/明细、施工地点/记录/照片。
- 新采购金额使用整数分，交易明细保留名称、规格、单位和价格快照。
- 新采购采用作废状态，不再直接物理删除。
- 每个应用连接已执行 `PRAGMA foreign_keys=ON`；实库 `foreign_key_check` 当前无异常。

仍会限制未来扩展的问题：

1. `projects.customer_name`、`projects.manager` 是自由文本，不能稳定关联客户和人员。
2. `products` 同时承担材料主数据和供应商报价，材料仍被绑定到单一供应商。
3. `suppliers` 只有一个联系人文本，且无法表达客户/供应商/分包商多重角色。
4. `work_logs.construction_site` 仍是文本，工天无法可靠关联项目、施工地点、班组和成本科目。
5. 旧采购与新采购并存且页面仍可写旧表，存在长期双写和统计口径分裂风险。
6. `construction_sites.site_name UNIQUE` 是全局唯一，两个项目不能出现同名的“一期”“主厂房”等地点；应改为项目内唯一。
7. 工人工资、旧采购金额仍使用 `REAL`，不适合作为财务汇总依据。
8. 供应商、产品等主数据仍可物理删除并级联删除，无法满足审计追溯。
9. 当前“迁移”仍写在 `database.py` 的初始化函数内，没有独立的、可测试和可回滚的迁移脚本。
10. 单例 SQLite 连接配合 `check_same_thread=False`，但没有完整的事务边界和并发写入序列化，不适合未来多用户服务化。

因此，当前 V2 只能视为兼容过渡层，不能继续在旧表上横向追加客户、合同、回款等字段。

## 3. 顶层数据边界

### 3.1 组织、人员与权限

- `organizations`：经营主体/法人公司。即使当前只有一家公司，也应预留 `organization_id`。
- `departments`：部门树。
- `users`：系统用户，不与工人档案混用。
- `employees`：企业员工与负责人档案。
- `roles`、`permissions`、`user_roles`：权限模型。
- `approval_definitions`、`approval_instances`、`approval_actions`：可追溯审批流。

业务表至少带 `organization_id`，避免未来多法人、多分公司时整体重构。

### 3.2 统一客商主数据

- `business_partners`：法人或自然人客商，包含统一社会信用代码、简称、状态和 `legacy_supplier_id`。
- `partner_roles`：`customer`、`supplier`、`subcontractor`、`other`，一个客商可有多个角色。
- `partner_contacts`：多联系人及部门、职务、电话、微信、邮箱。
- `partner_addresses`：注册地址、收货地址、开票地址、项目地址。
- `partner_bank_accounts`：银行账户；敏感字段在服务端版本加密。
- `partner_qualifications`：资质、证书、有效期和附件。

客商名称只用于展示和历史快照，业务关联一律使用 `partner_id`。

### 3.3 项目、WBS 与预算

- `projects`：项目编码、名称、客户 `customer_partner_id`、负责人 `manager_employee_id`、状态、计划/实际日期。
- `project_sites`：项目下的施工地点，唯一约束为 `(project_id, site_code)`，不再全局约束地点名称。
- `project_members`：人员、项目角色和有效期。
- `wbs_nodes`：项目工作分解结构，支持父子层级、排序和状态。
- `cost_codes`：企业统一成本科目树，如材料、人工、机械、运输、分包、管理费。
- `project_budget_versions`、`project_budget_items`：预算版本及按 WBS/成本科目的预算。
- `project_changes`：签证、变更、索赔，关联合同、预算和收入/成本影响。
- `project_milestones`：进度、验收和收款节点。

项目是主分析维度；项目外发生的业务允许 `project_id` 为空，但必须标记为“待归集”“库存”或“管理费用”等明确归属状态。

## 4. 合同与收入

合同建议使用统一单头，而不是为客户合同、采购合同、分包合同分别复制一套公共字段：

- `contracts`：合同号、类型 `customer/purchase/subcontract/other`、甲乙方、项目、币种、含税金额、签订日期、状态。
- `contract_versions`：原合同、补充协议和变更版本。
- `contract_items`：清单项，关联 WBS、材料或服务项，保存业务快照。
- `contract_milestones`：履约、开票、收款或付款节点。
- `contract_changes`：变更签证及审批状态。
- `contract_settlements`、`contract_settlement_items`：最终或阶段结算。

收入链路：

- `receivable_plans`：按合同节点生成的计划应收。
- `sales_invoices`、`sales_invoice_items`：销项发票及明细。
- `receipts`：银行实际回款流水。
- `receipt_allocations`：一笔回款对多个合同、发票或应收计划的核销关系。

不得仅在合同表保存“已回款金额”这类可由事实计算的累计字段；如为性能建立缓存，必须可重算并记录刷新时间。

## 5. 采购、库存与应付

采购链路：

- `materials`、`material_categories`、`units_of_measure`：企业统一材料/物料主数据。
- `supplier_offers`：供应商针对材料的未税报价、税率和有效期；替代“一个产品属于一个供应商”的旧模型。
- `purchase_requests`、`purchase_request_items`：项目请购。
- `rfqs`、`rfq_suppliers`、`supplier_quotations`、`supplier_quotation_items`：询价与比价历史。
- `purchase_orders`、`purchase_order_items`：采购订单；可关联项目、合同和请购单。项目采购成本按“含税材料额 + 采购单运费”归集，历史单据保留原金额且不追溯补税。
- `goods_receipts`、`goods_receipt_items`：到货与验收。
- `purchase_returns`、`purchase_return_items`：退货。
- `supplier_invoices`、`supplier_invoice_items`：进项发票。
- `payable_plans`：计划应付。
- `payments`：实际付款。
- `payment_allocations`：付款对合同、发票或应付计划的核销。

如未来需要库存，新增：

- `warehouses`、`stock_locations`、`inventory_movements`、`inventory_balances`。

采购订单不能直接等同于成本。材料成本应在验收/入库、领用或按企业会计口径确认时生成，避免订单取消后仍被计入成本。

## 6. 人工、分包与施工履约

- `workers`：工人档案，不直接保存会变化的唯一工资标准。
- `crews`、`crew_members`：班组及成员有效期。
- `worker_rate_versions`：工资标准及生效区间。
- `labor_entries`：日期、项目、地点、WBS、班组、工人、工天/工时和当日工资快照。
- `payroll_periods`、`payroll_items`：工资结算周期及个人结算结果。
- `subcontract_progress`、`subcontract_progress_items`：分包计量。
- `subcontract_settlements`：分包结算。
- `construction_records`：工程量、施工部位和工作内容。
- `inspection_records`、`inspection_issues`：验收、整改和复验记录。
- `attachments`、`attachment_links`：统一附件元数据与业务对象关系，不为每个模块重复建照片表。

施工工程量、人工工天、分包计量是不同业务事实，可以关联同一项目/WBS，但不能混在同一张表。

## 7. 成本与经营分析

- `cost_entries`：统一项目成本事实，包含组织、项目、WBS、成本科目、发生日期、币种、原币/本位币金额和过账状态。
- `cost_entry_sources`：成本与到货、工资、分包结算、费用单等来源的关联。
- `cost_allocations`、`cost_allocation_items`：总部费用、库存领用或跨项目成本的分摊单。
- `expense_claims`、`expense_claim_items`：报销及其他费用事实。
- `project_period_snapshots`：月末经营快照，仅用于看板加速，不作为原始凭证。

不要只用 `source_type + source_id` 作为无法受外键保护的多态关联。应通过来源关联表、统一单据注册表或按来源类型建立受约束的关联，确保来源可追溯。

关键指标均应有明确口径，例如：

- 合同额：有效合同及已批准变更的含税/不含税金额。
- 已完产值：审批通过的工程计量或进度确认。
- 已发生成本：已过账成本事实，不等同于采购订单金额。
- 应收、已开票、已回款、应付、已付款：分别来源于独立事实表。
- 预计毛利：收入预测减去已发生成本和剩余预计成本。

## 8. 通用字段与约束

新业务表遵守以下规则：

- 内部主键当前使用 `INTEGER`；同时从现在起增加不可变 `public_id`（UUID/ULID 文本），避免 API 化后补录全局标识。
- 金额使用整数最小币种单位 `*_minor`，并保存 `currency_code`；税率使用整数基点或定点小数。
- 数量、工时和工程量使用可迁移的定点小数口径；禁止用二进制浮点承担财务金额。
- 日期使用 `YYYY-MM-DD`；时间使用带时区的 ISO 8601，服务端统一 UTC。
- 每张业务表包含 `created_at`、`created_by`、`updated_at`、`updated_by`。
- 主数据使用 `is_active` 或 `deleted_at`；交易单据使用状态机、作废、红冲和调整记录。
- 单据号唯一范围至少包含 `organization_id`，不能假定全系统永久全局唯一。
- 外键列、单据号、业务日期、状态及常用组合筛选必须建立索引。
- `CHECK` 约束覆盖金额、数量、状态和日期逻辑；状态变更还需在服务层校验。
- 单头、明细、审批和台账过账必须位于同一事务。
- 所有表名和字段名使用稳定英文；中文只作为界面文案或字典值展示。

## 9. 数据库访问与迁移架构

将当前 `database.py` 拆为：

```text
db/
  connection.py
  migrations/
    0001_baseline.py
    0002_partner_project_core.py
    0003_material_purchase.py
  repositories/
services/
  project_service.py
  purchase_service.py
  labor_service.py
  contract_service.py
```

- 每次结构变更只通过独立、编号且可测试的迁移执行。
- 每次迁移前自动备份 SQLite 文件，并记录版本、校验结果和执行时间。
- 每个连接都执行 `PRAGMA foreign_keys=ON`、`busy_timeout`；写操作使用显式事务。
- 单机版避免跨线程共享同一连接；服务化后使用连接池和 PostgreSQL。
- 迁移完成后核对记录数、金额合计、工天合计、孤立外键及业务抽样。
- 禁止长期双写新旧表；过渡期由单一服务完成一次性切换并设置截止版本。

## 10. 分阶段实施顺序

### 阶段 0：冻结旧模型并建立安全底座

1. 不再为 `suppliers/products/purchases/work_logs` 增加业务字段。
2. 拆分连接和迁移框架，加入备份、事务、外键检查和迁移测试。
3. 为现有 V2 表补 `organization_id/public_id/audit fields` 的迁移方案。
4. 将 `construction_sites` 的唯一约束改为项目内唯一。
5. 确认旧采购页面下线日期，消除新旧采购双写入口。

### 阶段 1：统一主数据与项目底座

1. 建立组织、用户/员工、统一客商、联系人、项目、项目地点、WBS、成本科目。
2. 把现有供应商映射到客商及供应商角色，保留 `legacy_supplier_id`。
3. 把 `projects.customer_name/manager` 映射为外键；无法确认的数据进入待认领队列。
4. 将工天和施工记录关联到项目地点/WBS，不再写自由工地文本。

### 阶段 2：材料与采购正规化

1. 将 `products` 拆为材料主数据和供应商报价。
2. 将现有采购单切换到新客商、材料和项目模型。
3. 增加请购、询价、订单、到货、进项发票、应付与付款核销。
4. 按实际业务决定是否启用仓库和库存移动。

### 阶段 3：人工、分包与成本台账

1. 迁移 `work_logs` 到 `labor_entries`，固化当日工资快照。
2. 建立工资和分包计量/结算。
3. 建立统一成本事实、来源关系和分摊单。

### 阶段 4：合同、收入与资金

1. 建立统一合同、合同版本、变更、结算和履约节点。
2. 增加应收计划、销项发票、回款及核销。
3. 建立合同额、产值、开票、回款、成本、应付、付款和毛利看板。

## 11. 继续开发的架构门槛

在新增客户、合同、成本、回款页面前，至少完成阶段 0 和阶段 1。现有页面可以维护，但新增功能必须遵守以下门槛：

- 不再直接依赖客商名、项目名、工地名或负责人姓名做关联。
- 不再向旧采购和旧工天表增加字段。
- 不用采购订单金额直接代表项目成本。
- 不在合同或项目表堆叠开票、回款、付款累计字段。
- 任何新财务金额都不得使用 `REAL`。
- 任何新单据必须有组织、状态、审计字段、事务边界和可追溯来源。

下一项开发工作应是把项目、客商和材料页面切换为直接读写 V3 repository/service，并下线旧采购写入口；随后进入阶段 2 的请购、询价、采购订单、到货和应付链路。
