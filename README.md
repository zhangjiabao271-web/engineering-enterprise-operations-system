# 工程企业经营系统

[![Tests](https://github.com/zhangjiabao271-web/engineering-enterprise-operations-system/actions/workflows/tests.yml/badge.svg)](https://github.com/zhangjiabao271-web/engineering-enterprise-operations-system/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-986A3E.svg)](LICENSE)

面向小微工程企业的本机经营工作台：网页界面 + Python 业务服务 + SQLite，可像桌面软件一样独立窗口打开，也可在浏览器使用。只维护一套 Web 界面，旧 Tkinter/ttkbootstrap 界面已退役。

**产品北极星：项目经营可核算率** —— 让每一个独立项目都能回答"赚没赚钱、钱有没有回来、结论是否可信"。

## 主要功能

- **经营驾驶舱**：8类可点击指标，展示确认收入、项目回款、应收、开票、成本、在营可核算毛利及待办；近6个月走势、成本结构和客户欠款分布；明细可搜索、分页和返回
- **今日自检与主动关注**：按业务日期核对工天、采购，由人工标记录完／无业务／稍后处理；关注到期催款、完工待收款、成本归集和附件缺失，支持暂缓、忽略及依据变化后重新提醒
- **项目成本明细**：从项目经营核算直接查看材料、人工和其他费用明细；原项目工作空间保留兼容入口，不再重复占用主导航
- **合同、结算、开票与回款**：按客户查看合同及零星现金工程；顶部确认收入、回款、开票、未收可逐笔下钻，份额不重复；支持未签业务、后补合同及项目内顺序抵扣
- **成本台账**：统一查看采购、人工和手工其他成本，待归集成本可后续归入项目
- **客户与供应商档案**：客户收入贡献和未收、供应商采购额与采购单数；金额可下钻，不把采购已付款标记当作实际资金，不重复设置合同维护入口
- **供应商与产品管理**：产品报价（未税价 / 税率 / 含税价）默认引用供应商税率，按供应商筛选材料，支持报价对比
- **采购中心**：正式采购与零星采购双轨，支持耗材多项目均摊、Excel 导入导出、批量归集、作废留痕
- **工天看板**：工人档案、批量记工、重复拦截、任意日期范围的工人/月度矩阵，以及逐日出勤明细与 Excel 导出
- **发票附件与经营主体**：销项 PDF 本地识别、票号去重及已有记录补附件；独立进项票台账支持关联已有成本、人工确认主体和抵扣状态
- **施工记录与验收**：现场照片附件、验收状态流转、按月份/厂区/状态检索
- **项目经营核算**：项目独立核算，自动归集采购与人工成本，分别计算确认口径毛利、应收未收和经营现金净额
- **AI 经营助手**：查账模式结合本地事实、受控查询规划和计算验证，保持只读并显示依据；独立的协助录入模式支持自然语言多笔费用、采购、回款和工天，结构化清单经本地校验及人工确认后整批保存
- **业务附件与资料理解**：图片及PDF本机预览，PDF连续滚动阅读；可在明确模型授权后理解合同付款条件与资料证据，人工确认后才能形成计划
- **数据治理中心**：待归集采购 / 工天、待确认客商等数据缺口的集中处理入口

原资金预算跑道功能已停止使用；资金账户、真实收支、待收待付和独立历史旧账仍保留，不删除历史记录。

详细产品与架构说明见 [PRODUCT_PRD_V4.md](PRODUCT_PRD_V4.md)、[ARCHITECTURE_V4.md](ARCHITECTURE_V4.md) 和 [DATABASE_ARCHITECTURE.md](DATABASE_ARCHITECTURE.md)。

## 快速开始

环境要求：Windows，Python 3.10+；原生桌面窗口另需 Microsoft Edge WebView2。

```bash
# 1. 创建虚拟环境
python -m venv .venv

# 2. 安装依赖
.venv\Scripts\python -m pip install -r requirements.txt

# 3. 先体验隔离合成演示（不需要正式数据库）
run_web_demo.bat
```

演示每次创建新的合成账本；不会读取你的原有业务资料。原生桌面演示可安装可选依赖后运行：

```powershell
.venv\Scripts\python -m pip install -r requirements-desktop.txt
.venv\Scripts\python web_workbench/desktop.py --demo qa/my-first-demo
```

首次正式使用、目录中不存在 `supplier_data.db` 时，可明确创建空账本：

```powershell
.venv\Scripts\python -c "from db.schema import init_db; init_db()"
```

之后双击 `run_workbench.vbs` 打开可录入桌面工作台，或用 `run_web.bat` 打开只读浏览器入口。已有旧账本先备份、在隔离副本核对迁移，再切换；新版入口不会自动升级已有账本。详细说明见 [本机网页桌面版](docs/desktop-workbench.md)。

当前代码目标迁移版本为650。旧账本必须先在线完整备份，在隔离副本升级并对账；下方640脚本仅用于历史630→640专项，不能替代当前版本的完整升级。它仅增加自检、关注与助手草稿管理表。已完成迁移630且只缺640的账本，可先保存并关闭所有录入窗口，再单独演练：

```powershell
.venv\Scripts\python scripts/upgrade_proactive_workbench.py --output qa/proactive-rehearsal
```

该命令备份数据库及引用附件，在全新隔离副本中升级并逐表核对，正式库不变。核对通过后，使用另一全新目录并显式添加 `--apply` 才升级正式库。若还有其他待迁移版本，此脚本会拒绝执行，不能跳过原有升级流程。助手草稿先校验、后确认；模型问题、预收款或原合同随回款补足结算的影响必须核对，取消、过期或账本变化后不直接记账。

旧 `main.py`、`run.bat`、`pages/` 和 `ui/` 已移除，运行不再需要 Tcl/Tk 或 ttkbootstrap。Excel 模板、解析与导出使用独立服务；图片和 PDF 页面处理显式依赖 Pillow。详见 [旧界面退役说明](docs/legacy-tk-retirement.md)。

## AI 助手配置（可选）

1. 复制 `config.ini.example` 为 `config.ini`
2. 在 `config.ini` 中填入你的 DeepSeek API Key（从 <https://platform.deepseek.com> 获取）
3. 或在应用内 AI 页面点击"AI 设置"填写

界面保存的 API Key 使用 Windows 本机加密凭据存储，也兼容本地 `config.ini`。凭据只用于请求你配置的模型服务；配置与凭据文件均已被 `.gitignore` 排除，请勿提交。

## 数据存储

所有业务数据保存在程序目录下的 `supplier_data.db`（SQLite）中，建议定期备份。数据库结构升级前会自动保留 `supplier_data.backup_日期时间.db` 备份文件（最多保留最近 5 份）。

## 运行测试

```bash
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

pytest 自动从空库建立独立临时基准，数据库和附件均隔离；依赖本地历史业务数据的旧用例会明确跳过，不将真实数据作为公开测试夹具。旧 Tk 专用测试已移除，业务服务与 Web 操作测试继续保留。

前端无需打包；安装Node.js后可运行 `node scripts/test_cockpit_detail.mjs`，其他无数据库交互回归见 CI。CI覆盖Windows/Python 3.10与3.12以及静态脚本语法和前端专项；跳过的历史数据或环境限制用例不计为实际验收通过。真实 WebView2 窗口另在合成账本中验收，不以服务测试代替实际窗口检查。

## 使用边界

- 安装后预置的两个经营主体是匿名示例，使用前请核对并修改，不自动推断历史单据归属。
- 进项票台账不等于完整多主体账套，也不代替会计确认或税务申报；进项税不会自动改写原有成本和利润口径。
- 开票、回款、收入确认、成本和现金仍是独立业务事实，不能互相替代。
- 所有入口只监听本机；本版本不提供公网建站、跨设备同步或完整多用户账套。
- 外部模型默认未授权，配置密钥不代表自动授权发送资料；资料理解及回款计划仍须分别人工核对。

## 开源与安全

- 仓库不包含业务数据库、附件、备份或真实 API Key。
- `config.ini`、`.env`、数据库和附件目录均已从 Git 排除。
- 请勿在 Issue、日志或截图中提交客户资料、合同、财务数据或密钥。
- 安全问题请按 [SECURITY.md](SECURITY.md) 通过 GitHub 私密渠道报告。

贡献代码前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。版本变化记录见 [CHANGELOG.md](CHANGELOG.md)。

## 目录结构

```
web_workbench/     # 当前网页与原生桌面窗口入口
ai_engine.py       # AI 经营助手引擎
ai_client.py       # DeepSeek API 客户端
db/                # 基础建表、连接管理与编号迁移
services/          # 业务服务层（页面不直连数据库）
tests/             # pytest 测试套件
scripts/           # 冒烟与审计脚本
docs/              # 安装、使用与安全边界
design-system/     # 设计系统文档
```

## License

[MIT](LICENSE)
