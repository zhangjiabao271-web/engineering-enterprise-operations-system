"""Expense purpose is separate from project allocation and payment timing."""

GROUPS = {
    '工程施工': ('材料辅料', '外包施工', '机械租赁', '设计服务', '运输搬运', '设备维修', '工人伙食'),
    '车辆使用': ('燃油费', '车辆保养', '车辆保险', '停车过路'),
    '场地与日常运营': ('场地租金', '水电煤', '办公通信'),
    '管理与财务': ('代理记账', '税费社保', '利息手续费', '业务招待', '其他经营管理'),
    '生活与家庭': ('家庭日常', '个人保险'),
    '还款及大额支出': ('还款本金', '资产购置', '一次性支出待核实'),
}
CATEGORIES = [f'{group} / {item}' for group, items in GROUPS.items() for item in items]
NON_COST = {'生活与家庭 / 家庭日常', '生活与家庭 / 个人保险',
            '还款及大额支出 / 还款本金', '还款及大额支出 / 资产购置'}
DAILY = {'车辆使用 / 燃油费', '场地与日常运营 / 水电煤',
         '工程施工 / 工人伙食', '生活与家庭 / 家庭日常'}


def suggestion(row):
    """Only unambiguous expense purposes are eligible for automatic relabeling."""
    category = row['category']
    if category in CATEGORIES:
        return category, True
    text = ' '.join(str(row.get(k) or '') for k in ('counterparty_name_snapshot', 'notes'))
    for token, target in (
        ('购房欠款', '还款及大额支出 / 一次性支出待核实'),
        ('车贷', '还款及大额支出 / 一次性支出待核实'),
        ('燃油', '车辆使用 / 燃油费'), ('汽车保养', '车辆使用 / 车辆保养'),
        ('汽车保险', '车辆使用 / 车辆保险'), ('代理记账', '管理与财务 / 代理记账'),
        ('贴息', '管理与财务 / 利息手续费'), ('贴现', '管理与财务 / 利息手续费'),
        ('外包施工队', '工程施工 / 外包施工'), ('设计费', '工程施工 / 设计服务'),
        ('电焊条', '工程施工 / 材料辅料'), ('电葫芦', '工程施工 / 设备维修'),
        ('招待费', '管理与财务 / 业务招待'),
    ):
        if token in text:
            return target, target != '还款及大额支出 / 一次性支出待核实'
    mapping = {'车辆燃油费': '车辆使用 / 燃油费', '外包施工费': '工程施工 / 外包施工',
               '分包费': '工程施工 / 外包施工', '机械费': '工程施工 / 机械租赁',
               '设备租赁费': '工程施工 / 机械租赁', '运输费': '工程施工 / 运输搬运',
               '水电煤': '场地与日常运营 / 水电煤'}
    return (mapping[category], True) if category in mapping else ('待确认用途', False)
