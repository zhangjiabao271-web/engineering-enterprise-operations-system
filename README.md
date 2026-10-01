# 工程企业经营系统

[![Tests](https://github.com/zhangjiabao271-web/engineering-enterprise-operations-system/actions/workflows/tests.yml/badge.svg)](https://github.com/zhangjiabao271-web/engineering-enterprise-operations-system/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-986A3E.svg)](LICENSE)

面向小微工程企业的单机桌面经营管理系统（Tkinter + ttkbootstrap + SQLite）。

**产品北极星：项目经营可核算率** —— 让每一个独立项目都能回答"赚没赚钱、钱有没有回来、结论是否可信"。

## 主要功能

- **经营驾驶舱**：以项目经营可核算率为北极星指标，分开展示已确认项目毛利、应收未收和未归集成本，逐项目展示经营阶段、结算、成本、毛利、现金和数据缺口
- **项目成本明细**：从项目经营核算直接查看材料、人工和其他费用明细；原项目工作空间保留兼容入口，不再重复占用主导航
- **合同、结算、开票与回款**：支持年度框架合同、单项目合同和补充协议；分配、结算、开票、回款全程带超额护栏
- **成本台账**：统一查看采购、人工和手工其他成本，待归集成本可后续归入项目
- **供应商与产品管理**：供应商档案、产品报价（未税价 / 税率 / 含税价）、报价对比
- **采购中心**：正式采购与零星采购双轨，支持 Excel 导入导出、批量归集、作废留痕
- **工天看板**：工人档案、批量记工、重复拦截、任意日期范围的工人/月度矩阵，以及逐日出勤明细与 Excel 导出
- **发票附件与经营主体**：销项 PDF 本地识别、票号去重及已有记录补附件；独立进项票台账支持关联已有成本、人工确认主体和抵扣状态
- **施工记录与验收**：现场照片附件、验收状态流转、按月份/厂区/状态检索
- **项目经营核算**：项目独立核算，自动归集采购与人工成本，分别计算确认口径毛利、应收未收和经营现金净额
- **AI 经营助手**：结合本地事实查询、受控查询规划和计算验证，支持 DeepSeek 只读经营分析，不修改任何业务记录
- **数据治理中心**：待归集采购 / 工天、待确认客商等数据缺口的集中处理入口

详细产品与架构说明见 [PRODUCT_PRD_V4.md](PRODUCT_PRD_V4.md)、[ARCHITECTURE_V4.md](ARCHITECTURE_V4.md) 和 [DATABASE_ARCHITECTURE.md](DATABASE_ARCHITECTURE.md)。

## 快速开始

环境要求：Windows，Python 3.10+

```bash
# 1. 创建虚拟环境
python -m venv .venv

# 2. 安装依赖
.venv\Scripts\python -m pip install -r requirements.txt

# 3. 启动
.venv\Scripts\python main.py
```

也可以直接双击 `run.bat` 启动。脚本会检查虚拟环境，并只在虚拟环境自带 Tcl/Tk 时设置对应路径。

如果系统提示缺少 Tkinter/Tcl，请从 <https://www.python.org/downloads/windows/> 安装官方 Windows Python，并确保安装时包含 Tcl/Tk 支持。

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

pytest 自动从空库建立独立临时基准，数据库和附件均隔离；依赖本地历史业务数据的旧用例会明确跳过，不将真实数据作为公开测试夹具。GUI 测试需要单独启用并准备可用的 Tcl/Tk 桌面环境。

## 使用边界

- 安装后预置的两个经营主体是匿名示例，使用前请核对并修改，不自动推断历史单据归属。
- 进项票台账不等于完整多主体账套，也不代替会计确认或税务申报；进项税不会自动改写原有成本和利润口径。
- 开票、回款、收入确认、成本和现金仍是独立业务事实，不能互相替代。

## 开源与安全

- 仓库不包含业务数据库、附件、备份或真实 API Key。
- `config.ini`、`.env`、数据库和附件目录均已从 Git 排除。
- 请勿在 Issue、日志或截图中提交客户资料、合同、财务数据或密钥。
- 安全问题请按 [SECURITY.md](SECURITY.md) 通过 GitHub 私密渠道报告。

贡献代码前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。版本变化记录见 [CHANGELOG.md](CHANGELOG.md)。

## 目录结构

```
main.py            # 应用入口与导航
ai_engine.py       # AI 经营助手引擎
ai_client.py       # DeepSeek API 客户端
db/                # 基础建表、连接管理与编号迁移
services/          # 业务服务层（页面不直连数据库）
pages/             # 页面层
ui/                # 通用 UI 组件
tests/             # pytest 测试套件
scripts/           # 冒烟与审计脚本
design-system/     # 设计系统文档
```

## License

[MIT](LICENSE)
