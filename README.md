# 工程企业经营系统

[![Tests](https://github.com/zhangjiabao271-web/engineering-enterprise-operations-system/actions/workflows/tests.yml/badge.svg)](https://github.com/zhangjiabao271-web/engineering-enterprise-operations-system/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-986A3E.svg)](LICENSE)

面向小微工程企业的单机桌面经营管理系统（Tkinter + ttkbootstrap + SQLite）。

**产品北极星：项目经营可核算率** —— 让每一个独立项目都能回答"赚没赚钱、钱有没有回来、结论是否可信"。

## 主要功能

- **经营驾驶舱**：以项目经营可核算率为北极星指标，分开展示已确认项目毛利、应收未收和未归集成本，逐项目展示经营阶段、结算、成本、毛利、现金和数据缺口
- **项目工作空间**：在一个项目中集中查看合同分配、结算、采购、人工、其他成本、发票和回款
- **合同、结算、开票与回款**：支持年度框架合同、单项目合同和补充协议；分配、结算、开票、回款全程带超额护栏
- **成本台账**：统一查看采购、人工和手工其他成本，待归集成本可后续归入项目
- **供应商与产品管理**：供应商档案、产品报价（未税价 / 税率 / 含税价）、报价对比
- **采购中心**：正式采购与零星采购双轨，支持 Excel 导入导出、批量归集、作废留痕
- **工天看板**：工人档案、批量记工、重复拦截、月度汇总与 Excel 导出
- **施工记录与验收**：现场照片附件、验收状态流转、按月份/厂区/状态检索
- **项目经营核算**：项目独立核算，自动归集采购与人工成本，分别计算确认口径毛利、应收未收和经营现金净额
- **AI 经营助手**：基于 DeepSeek 对本机经营数据做只读分析，不修改任何业务记录
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

API Key 仅保存在本机 `config.ini` 中，不会上传到 DeepSeek 之外的服务。`config.ini` 已被 `.gitignore` 排除，请勿提交。

## 数据存储

所有业务数据保存在程序目录下的 `supplier_data.db`（SQLite）中，建议定期备份。数据库结构升级前会自动保留 `supplier_data.backup_日期时间.db` 备份文件（最多保留最近 5 份）。

## 运行测试

```bash
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

测试在独立临时数据库上运行（无生产库时自动从空库初始化），不触碰本机业务数据。

## 开源与安全

- 仓库不包含业务数据库、附件、备份或真实 API Key。
- `config.ini`、`.env`、数据库和附件目录均已从 Git 排除。
- 请勿在 Issue、日志或截图中提交客户资料、合同、财务数据或密钥。
- 安全问题请按 [SECURITY.md](SECURITY.md) 通过 GitHub 私密渠道报告。

贡献代码前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。版本变化记录见 [CHANGELOG.md](CHANGELOG.md)。

## 目录结构

```
main.py            # 应用入口与导航
database.py        # 基础建表与初始化
ai_engine.py       # AI 经营助手引擎
ai_client.py       # DeepSeek API 客户端
db/                # 连接管理与迁移
services/          # 业务服务层（页面不直连数据库）
pages/             # 页面层
ui/                # 通用 UI 组件
tests/             # pytest 测试套件
scripts/           # 冒烟与审计脚本
design-system/     # 设计系统文档
```

## License

[MIT](LICENSE)
