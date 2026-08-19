# 贡献指南

感谢你改进工程企业经营系统。

## 开发环境

项目面向 Windows，要求 Python 3.10 或更高版本。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

## 修改原则

- 页面通过 `services/` 访问业务逻辑，不直接操作数据库。
- 金额以整数分存储，界面统一显示两位小数。
- 已发生业务使用作废、调整或修订记录，不直接删除历史事实。
- 合同额、收入确认、发票、回款、成本和现金必须保持独立口径。
- 数据库结构变化必须新增迁移，并覆盖空库初始化和历史升级场景。
- 新功能或缺陷修复应增加相应测试。

## 数据与隐私

只使用虚构测试数据。禁止提交真实数据库、附件、导出文件、API Key、客户资料、合同、财务数据或个人联系方式。

## 提交 Pull Request

1. 从 `main` 创建功能分支。
2. 保持修改范围单一，并写清业务原因和影响。
3. 运行完整测试，确认 `git diff --check` 无错误。
4. 在 PR 中说明验证方式、数据库迁移影响和界面变化。

提交即表示你同意按本项目的 [MIT License](LICENSE) 发布贡献。
