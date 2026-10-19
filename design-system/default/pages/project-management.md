# Project Management Page Override

- Use a master-detail layout: project list is the primary region; construction sites are the selected project's secondary region.
- Default to the `在建项目` tab with only `筹备中 / 进行中`; keep `已完工 / 已关闭` in a one-click `历史项目` tab.
- Project status and collection status are independent: a completed project may still remain in the finance pending-collection queue.
- Keep project status visible as text. Color may reinforce status but must not be the only signal.
- Primary CTA is `新增项目`; editing and closing are visually subordinate.
- Project creation/editing uses a centered modal with visible labels, grouped dates and inline validation.
- Preserve list context after save by reselecting the saved project.
- 网页长列表在可视区底部提供同步横向悬浮滚动条，无需滚到表尾；详情或跨页业务返回时恢复原筛选、分页及横向位置。
- Closing a project and deactivating a site require confirmation and retain historical records.
- Tables use the global DPI-scaled 38px row height and avoid adding optional columns that force horizontal overflow.
- At 1200px width, project and site tables remain vertically stacked.
- Follow the shared desktop style: `新增项目` is the blue primary action; secondary actions use quiet rounded outlines.
- Editing, closing, site maintenance and deactivation use neutral outline actions.
- Do not render project statuses as high-contrast square chips or tint entire active rows.
- Express status with plain text; use muted text only for closed or inactive historical records.
- Form dialogs use the same light surface as the application, with no contrasting frame behind field labels.
- 正式合同工程允许修正为零星现金工程：保存前明确提示同步影响，服务层在同一事务内作废有效合同项目分配、将有效收入确认改为无需合同、保留回款金额并解除合同归属。
- 采购、成本、工天、施工记录始终归属于项目，业务模式调整不得改写这些记录。
- 已有有效发票、回款关联发票、回款未明确对应一笔收入确认或与其他项目共用回款时，禁止直接改为零星现金工程并给出具体原因。
- 零星现金工程已有收入或回款时，不允许直接反向改为正式合同工程；该方向需要先建立明确的合同项目关系后另行迁移。
- 零星现金工程可填写“约定总额”；留空表示允许按实际完工分次持续确认，填写后累计有效完工确认不得超过该金额。
- 项目从正式合同工程转为零星现金工程且未手填约定总额时，优先带入原固定合同项目边界或现有收入确认合计。
- 编辑零星现金项目时显示当前已确认金额；约定总额不得调低到有效完工确认合计以下。

## 新版工作台项目台账

- 项目与有效关联合同须属于同一客户。已有合同关系、收入、发票或回款时，不直接更换或清空已知客户；未使用项目仍允许更正，未知客户可按明确一致的关系补齐。已有成本不单独冻结客户资料。

- 主列表直接显示每个项目的已确认收入、已回款、已开票、未回款，金额取共享财务事实，不用合同金额或施工产值替代收入。
- 项目名称、客户与状态合并为可换行的身份列；金额使用等宽数字并保持完整可见，不把计划日期放在财务主列之前。
- 每个金额均为可聚焦按钮，零金额也可打开对应空态。收入、回款、发票与待收明细在同一个只读弹窗内切换。
- 回款一笔只列一次，同时说明整笔到账和本项目归属金额；合计按本项目有效分配计算，排除作废回款与作废分配。
- 明细按日期倒序列出全周期记录，显示来源编号、金额与业务依据，并提供原记录及附件入口；原记录有返回上一级明细入口。
- 点击金额不更改顶栏项目范围、主列表筛选、分页和滚动位置；返回台账关闭弹窗即可。
- 项目台账表头滚出视野时吸附在顶部工具栏下方，列宽和横向位置与原表严格同步；页面滚过表格末尾、切页及明细弹窗打开时退出，不覆盖其他内容。
- 默认在建项目，历史项目与全部项目一键切换；指定顶栏项目范围时直接展示所选项目，不因其完工状态隐藏。
- 台账导出保留财务金额和完整项目字段。界面调整不写正式账本，也不新增收入、回款或发票。
