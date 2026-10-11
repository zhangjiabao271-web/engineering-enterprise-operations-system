"""Readable local report generated from a saved, traceable analysis snapshot."""

from html import escape
from pathlib import Path

from db.connection import db_read
from services.document_cashflow_service import get_analysis


def money(value):
    if value is None:
        return '待补充'
    sign = '-' if value < 0 else ''
    value = abs(value)
    return f'{sign}¥{value // 100:,}.{value % 100:02d}'


def report_text(analysis):
    payload = analysis['payload']
    labels = {'draft': '草稿', 'confirmed': '已确认', 'cancelled': '已取消'}
    sections = [f'资料现金流分析 #{analysis["id"]} · {labels[analysis["status"]]}',
                f'合同编号ID：{analysis["contract_id"] or "未指定"} / 项目ID：{analysis["project_id"] or "整份合同口径"}',
                f'生成：{analysis["created_at"]} / 规则版本：{analysis["engine_version"]}',
                '有效性：' + (analysis['stale_reason'] or '来源版本未变化，仍须人工核对'),
                '分析指纹：' + analysis['fingerprint'],
                '\n'.join(payload['limitations'])]
    for item in payload.get('interpretations', []):
        sections.append(f'\n资料业务理解：{item["source"]["name"]} / {item["model"]}\n{item["summary"]}')
        if item['unreadable_sources']:
            sections.append('未读清来源：' + '、'.join(item['unreadable_sources']))
    for label, key in (('按资料日期估算', 'base'), ('用户设定情景估算', 'scenario')):
        data = payload[key]
        assumption = data['assumptions']
        sections.append(f'\n{label}\n{data["basis"]}\n启动资金 {money(assumption["opening_minor"])}；'
                        f'回款延后 {assumption["income_delay_days"]} 天；支出增加 {assumption["expense_increase_percent"]}%\n'
                        f'日末垫资缺口 {money(data["gap_minor"])}；同日先支后收缺口 {money(data["outflow_first_gap_minor"])}\n'
                        f'未计入节点 {len(data["excluded_keys"])} 个。若有未知或未识别条款，该缺口不代表完整资金需求。')
        for event in data['schedule']:
            sections.append(f'{event["date"]} | 收 {money(event["in_minor"])} | 支 {money(event["out_minor"])} | '
                            f'余额 {money(event["balance_minor"])} | 节点 {", ".join(event["row_keys"])}')
    sections.append('\n收付节点与全部来源（时间表通过节点编号回溯）')
    for row in payload['rows']:
        direction = {'in': '收款', 'out': '付款'}.get(row['direction'], '方向待核对')
        sections.append(f'\n[{row["key"]}] {direction} {row["title"]} / {money(row["amount_minor"])} / '
                        f'{row["due_date"] or row.get("timing") or "日期待补充"}\n条件：{row["condition"]}\n'
                        f'核对项：{"；".join(row["issues"]) or "待人工确认"}')
        if row.get('calculation'):
            sections.append('系统复算：' + row['calculation'])
        for source in row['sources']:
            sections.append(f'来源：{source["name"]} v{source["version"]} / {source["locator"]}\n'
                            f'SHA-256 {source["sha256"]}\n原文：{source["excerpt"]}')
    sections.append('\n注意事项与协商依据（规则提示，需结合完整资料人工判断）')
    for note in payload['notes']:
        source = note['source']
        sections.append(f'{note["text"]}\n来源：{source["name"]} v{source["version"]} / {source["locator"]}\n'
                        f'SHA-256 {source["sha256"]}\n原文：{source.get("excerpt") or source.get("text") or "见该资料快照全文"}')
    sections.append('\n正式计划：' + ('、'.join(map(str, analysis['plan_ids'])) or '未生成'))
    sections.append('本报告是导出时的分析快照；来源更新后请回工作台核对有效性。')
    return '\n'.join(sections)


def confirmed_plans_text(analysis_id):
    with db_read() as conn:
        plans = [dict(row) for row in conn.execute('''SELECT p.*,l.row_key FROM cashflow_plan_links l
            JOIN fund_plans p ON p.id=l.plan_id WHERE l.analysis_id=? ORDER BY p.id''', (analysis_id,))]
    if not plans:
        return '此分析尚未生成正式收付计划。'
    return '\n\n'.join(f'计划 #{p["id"]} · {p["title"]} · {p["status"]}\n'
                       f'{p["due_date"] or "待安排"} / {money(p["amount_minor"])} / 节点 {p["row_key"]}\n'
                       f'确认来源：{p["notes"]}' for p in plans)


def export_html(analysis_id, path):
    analysis = get_analysis(analysis_id)
    payload = analysis['payload']
    labels = {'draft': '分析草稿', 'confirmed': '已确认计划', 'cancelled': '已取消草稿'}
    def table(data):
        items = []
        for event in data['schedule']:
            sources = ' '.join(f'<a href="#node-{key}">{key[:8]}</a>' for key in event['row_keys'])
            items.append(f'<tr><td>{escape(event["date"])}</td><td class="amount">{money(event["in_minor"])}</td>'
                         f'<td class="amount">{money(event["out_minor"])}</td><td class="amount">{money(event["balance_minor"])}</td>'
                         f'<td>{sources}</td></tr>')
        if not items:
            items.append('<tr><td colspan="5">没有可计入的明确节点，请补充资料并核对冲突。</td></tr>')
        return '<div class="table-wrap"><table><thead><tr><th>日期</th><th>计划收款</th><th>计划付款</th><th>预计余额</th><th>节点来源</th></tr></thead><tbody>' + ''.join(items) + '</tbody></table></div>'
    node_cards = []
    for row in payload['rows']:
        evidence = ''.join('<div class="evidence"><strong>' + escape(f'{source["name"]} · v{source["version"]} · {source["locator"]}') +
                           '</strong><p>' + escape(source['excerpt']) + '</p><small>SHA-256 ' + escape(source['sha256']) + '</small></div>' for source in row['sources'])
        status = '资料冲突' if row['conflict'] else ('待补充' if row['issues'] else '待人工确认')
        direction = {'in': '收款', 'out': '付款'}.get(row['direction'], '方向待核对')
        node_cards.append(f'<article class="node" id="node-{row["key"]}"><div class="node-head"><h3>{escape(direction + " · " + row["title"])}</h3>'
                          f'<span class="status">{status}</span></div><p class="node-values">{money(row["amount_minor"])} <span>/</span> '
                          f'{escape(row["due_date"] or row.get("timing") or "日期待补充")}</p><p>前置条件：{escape(row["condition"])}</p>'
                          f'<p class="muted">{escape(row.get("calculation", ""))}</p>'
                          f'<p class="muted">{escape("；".join(row["issues"]) or "核对原文、前置条件及已有计划后才能转入正式计划")}</p>'
                          f'<details><summary>查看 {len(row["sources"])} 处原文与版本依据</summary>{evidence}</details></article>')
    notes = []
    for note in payload['notes']:
        source = note['source']
        notes.append('<article class="evidence"><p>' + escape(note['text']) + '</p><strong>' +
                     escape(f'{source["name"]} v{source["version"]} / {source["locator"]}') + '</strong><p>' +
                     escape(source.get('excerpt') or source.get('text') or '见工作台资料快照全文') + '</p><small>SHA-256 ' + escape(source['sha256']) + '</small></article>')
    understanding_cards = ''.join('<section class="panel"><h2>资料业务理解</h2><p class="muted">' +
        escape(item['source']['name'] + ' / ' + item['model']) + '</p><p>' + escape(item['summary']) + '</p>' +
        ('<p class="validity">未读清来源：' + escape('、'.join(item['unreadable_sources'])) + '</p>'
         if item['unreadable_sources'] else '') + '</section>' for item in payload.get('interpretations', []))
    base, scenario = payload['base'], payload['scenario']
    assumptions = scenario['assumptions']
    content = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>资料现金流分析 #{analysis_id}</title>
<style>
:root{{color-scheme:light;--blue:#0066cc;--ink:#1d1d1f;--muted:#63636b;--line:#dedee5;--motion:160ms}}
*{{box-sizing:border-box}}body{{margin:0;color:var(--ink);font:15px/1.7 "Microsoft YaHei UI",sans-serif;background:#f5f5f7}}
body:before{{content:"";position:fixed;inset:0;z-index:-1;background:radial-gradient(ellipse at 15% 0%,#eaf3ff,transparent 52%),radial-gradient(ellipse at 95% 10%,#edf7f1,transparent 38%)}}
main{{max-width:1160px;margin:32px auto;padding:0 28px 48px}}header{{padding:28px 32px;border-radius:22px;background:rgba(255,255,255,.84);backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,.9);box-shadow:0 1px 3px #1d1d1f0a,0 12px 36px #0066cc08}}
.eyebrow{{color:var(--blue);font-size:13px;letter-spacing:.1em}}h1{{font-size:30px;line-height:1.3;margin:8px 0 10px;font-weight:600}}h2{{font-size:21px;margin:0}}h3{{font-size:16px;margin:0;font-weight:600}}p{{margin:6px 0}}.muted,small{{color:var(--muted)}}small{{overflow-wrap:anywhere;font-size:12px}}
.status{{display:inline-block;background:#f1f4f8;border:1px solid #dde5ef;border-radius:99px;padding:2px 10px;font-size:12px;white-space:nowrap}}.validity{{margin-top:16px;border-left:3px solid #956820;padding-left:12px;color:#735114}}
.metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:22px 0}}.metric{{padding:22px 24px;background:white;border:1px solid var(--line);border-radius:16px}}.metric strong{{display:block;font:600 30px/1.5 "Segoe UI",sans-serif;font-variant-numeric:tabular-nums}}.metric span{{color:var(--muted);font-size:13px}}
.panel{{background:white;border:1px solid var(--line);border-radius:18px;padding:24px;margin:18px 0}}.panel-head{{display:flex;justify-content:space-between;gap:16px;align-items:center;margin-bottom:16px}}.segmented{{display:flex;gap:4px;padding:4px;border-radius:12px;background:#f5f5f7;border:1px solid var(--line)}}button{{font:inherit;border:0;cursor:pointer;border-radius:8px;padding:7px 14px;background:transparent;color:var(--muted);transition:transform 120ms cubic-bezier(.2,.8,.2,1)}}button[aria-pressed="true"]{{background:white;color:var(--blue);box-shadow:0 1px 4px #1d1d1f12}}button:active{{transform:scale(.98)}}button:focus-visible,a:focus-visible,summary:focus-visible{{outline:2px solid var(--blue);outline-offset:3px}}
.table-wrap{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;white-space:nowrap;font-size:14px}}th{{color:var(--muted);font-weight:500;text-align:left;background:#f8f9fb}}td,th{{padding:13px 16px;border-bottom:1px solid #edf0f3}}th:nth-child(2),th:nth-child(3),th:nth-child(4){{text-align:right}}.amount{{text-align:right;font-family:"Segoe UI",sans-serif;font-variant-numeric:tabular-nums}}a{{color:var(--blue);text-underline-offset:3px;font-size:12px}}.scenario-note{{margin:14px 0;color:var(--muted);font-size:13px}}
.nodes{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}}.node{{padding:22px;background:white;border:1px solid var(--line);border-radius:16px;scroll-margin-top:24px}}.node-head{{display:flex;align-items:center;justify-content:space-between;gap:12px}}.node-values{{font:600 23px/1.8 "Segoe UI",sans-serif;margin:12px 0 4px}}.node-values span{{font-weight:400;color:#afb5bf;font-size:18px}}details{{margin-top:16px;border-top:1px solid #edf0f3;padding-top:12px}}summary{{cursor:pointer;color:var(--blue);font-size:13px}}.evidence{{padding:14px 0;border-bottom:1px solid #edf0f3}}.evidence p{{font-size:14px;overflow-wrap:anywhere}}footer{{margin-top:28px;color:var(--muted);font-size:12px}}.toolbar{{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-top:18px}}.motion-control{{font-size:12px;color:var(--muted)}}[hidden]{{display:none!important}}
.reveal{{opacity:.65;transition:opacity var(--motion) cubic-bezier(.2,.8,.2,1)}}.reveal.ready{{opacity:1}}.reduced *{{animation:none!important;transition:none!important}}.reduced button:active{{transform:none}}
@media(prefers-reduced-motion:reduce){{*{{animation:none!important;transition:none!important;scroll-behavior:auto!important}}button:active{{transform:none}}}}
@media(max-width:760px){{main{{padding:0 16px;margin:16px auto}}header{{padding:22px}}h1{{font-size:25px}}.metrics,.nodes{{grid-template-columns:1fr}}.panel-head{{align-items:flex-start;flex-direction:column}}.panel{{padding:18px}}}}
@media print{{body:before,.toolbar,.segmented{{display:none}}main{{margin:0;max-width:none;padding:0}}header{{background:white;backdrop-filter:none;box-shadow:none;border:0}}.panel,.node,.metric{{break-inside:avoid}}.nodes{{display:block}}.node{{margin:12px 0}}}}
</style></head><body><main>
<header><div class="eyebrow">工程经营 · 合同与项目资料</div><h1>先看资金节奏，再确认收付计划</h1><p>资料现金流分析 #{analysis_id} <span class="status">{labels[analysis['status']]}</span></p>
<p class="muted">合同ID {analysis['contract_id'] or '未指定'} / 项目ID {analysis['project_id'] or '整份合同口径'} · {escape(analysis['created_at'])}</p>
<p class="validity">{escape(analysis['stale_reason'] or '估算以各节点前置条件按期满足为前提，资料中的未知与冲突仍需人工核对。')}</p>
<div class="toolbar"><span class="motion-control"><label><input id="reduce-motion" type="checkbox"> 减少动态效果</label></span><button id="print-report" type="button">打印 / 保存 PDF</button></div></header>
{understanding_cards}
<section class="metrics" aria-label="垫资估算"><article class="metric"><span>按资料日期 · 日末垫资缺口</span><strong>{money(base['gap_minor'])}</strong><span>同日先支后收：{money(base['outflow_first_gap_minor'])}</span></article>
<article class="metric"><span>设定情景 · 日末垫资缺口</span><strong>{money(scenario['gap_minor'])}</strong><span>回款延后 {assumptions['income_delay_days']} 天 / 支出增加 {assumptions['expense_increase_percent']}%</span></article>
<article class="metric"><span>待补充 / 冲突 · 未计入时间表</span><strong>{len(base['excluded_keys'])} <small>项</small></strong><span>启动可用资金：{money(assumptions['opening_minor'])}</span></article></section>
<section class="panel"><div class="panel-head"><h2>收支时间表</h2><div class="segmented" aria-label="估算方案"><button type="button" data-view="base" aria-pressed="true">按资料日期</button><button type="button" data-view="scenario" aria-pressed="false">设定情景</button></div></div>
<div id="schedule-base">{table(base)}</div><div id="schedule-scenario" hidden>{table(scenario)}</div><p class="scenario-note">{escape(base['basis'])} 未识别条款和总分项重复须另行核对，缺口不代表完整资金需求。</p></section>
<div class="panel-head"><h2>逐项核对节点与来源</h2><span class="muted">点击原文依据，核对条款与版本</span></div><section class="nodes">{''.join(node_cards) or '<p>未识别到明确收付节点，请在工作台补充资料。</p>'}</section>
<section class="panel"><h2>注意事项与协商依据</h2>{''.join(notes) or '<p class="muted">本地规则未提取更多注意事项，请人工核对完整资料。</p>'}</section>
<section class="panel"><h2>分析范围与确认边界</h2><ul>{''.join('<li>' + escape(item) + '</li>' for item in payload['limitations'])}</ul><p>已生成正式计划：{escape('、'.join(map(str, analysis['plan_ids'])) or '尚未确认')}</p><p class="muted">本报告不能确认或修改账目。请回工作台逐项核对后确认；情景假设不会写入正式计划。</p></section>
<footer>本地报告 · 无联网请求 · 导出时的来源快照 · 原资料变化后请回工作台重新核对<br>规则版本 {escape(analysis['engine_version'])} / 分析指纹 {escape(analysis['fingerprint'])}</footer>
</main><script>
const reduce = document.querySelector('#reduce-motion');
reduce.checked = matchMedia('(prefers-reduced-motion: reduce)').matches;
reduce.addEventListener('change', () => document.body.classList.toggle('reduced', reduce.checked));
if (reduce.checked) document.body.classList.add('reduced');
document.querySelector('#print-report').addEventListener('click', () => window.print());
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', event => {{
  const view = button.dataset.view;
  document.querySelectorAll('[data-view]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
  ['base', 'scenario'].forEach(key => {{document.querySelector('#schedule-' + key).hidden = key !== view;}});
  const panel = document.querySelector('#schedule-' + view);
  panel.classList.remove('reveal', 'ready');
  if (event.detail > 0 && !reduce.checked) {{panel.classList.add('reveal'); requestAnimationFrame(() => panel.classList.add('ready'));}}
}}));
</script></body></html>'''
    Path(path).write_text(content, encoding='utf-8')
    return path
