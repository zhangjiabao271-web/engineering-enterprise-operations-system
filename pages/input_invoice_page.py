"""Local input-invoice and legal-entity workbench; existing ledgers stay intact."""
from datetime import date
from decimal import Decimal
from tkinter import filedialog, messagebox, simpledialog
import os

import ttkbootstrap as ttk
from ttkbootstrap.constants import E, W
from services import operating_entity_service as entities
from services import input_invoice_service as invoices
from services.master_data_service import list_suppliers
from ui.components import DataTable, DatePicker
from ui.dialogs import build_form_dialog, add_form_actions


def money(value):
    return f'{int(value or 0)/100:,.2f}'


def table(parent, columns):
    amount_columns={'amount','gross','tax','linked','remaining','available','missing'}
    specs=tuple((key,title,width,E if key in amount_columns else W)
                for key,title,width in columns)
    return DataTable(parent,specs,empty_text='当前范围暂无记录',
                     stretch=(),horizontal=True,padding=0)


class EntityPage:
    def __init__(self,parent):
        self.parent=parent
        self.entities={r['name']:r for r in entities.list_entities()}
        self.names={r['id']:r['name'] for r in self.entities.values()}
        ttk.Label(parent,text='先确认项目归属建议，再按类别批量确认单据。只改归属标签，不改金额和历史回款匹配；新单据也需人工确认。',wraplength=900).pack(anchor='w')
        ttk.Label(parent,text='示例一般纳税人主体：一般纳税人；示例小规模主体：小规模纳税人。未确认的记录不会归给任何一家。',style='PageSub.TLabel').pack(anchor='w',pady=(4,8))
        bar=ttk.Frame(parent)
        bar.pack(fill='x')
        self.kind=ttk.StringVar(value='项目归属建议')
        self.kinds={v[0]:k for k,v in entities.RECORDS.items()}
        combo=ttk.Combobox(bar,textvariable=self.kind,values=list(self.kinds),state='readonly',width=16)
        combo.pack(side='left',padx=(0,8))
        combo.bind('<<ComboboxSelected>>',lambda e:self.refresh())
        self.keyword=ttk.StringVar()
        entry=ttk.Entry(bar,textvariable=self.keyword,width=22)
        entry.pack(side='left',padx=4)
        entry.bind('<Return>',lambda e:self.refresh())
        self.pending=ttk.BooleanVar(value=True)
        ttk.Checkbutton(bar,text='只看待确认',variable=self.pending,command=self.refresh).pack(side='left',padx=8)
        ttk.Button(bar,text='查询 / 刷新',command=self.refresh).pack(side='left')
        actions=ttk.Frame(parent)
        actions.pack(fill='x',pady=8)
        self.entity=ttk.StringVar(value=next(iter(self.entities)))
        ttk.Combobox(actions,textvariable=self.entity,values=list(self.entities),state='readonly',width=36).pack(side='left')
        ttk.Button(actions,text='确认选中记录归属',command=self.assign).pack(side='left',padx=8)
        ttk.Button(actions,text='全选当前列表',command=lambda:self.tree.selection_set(self.tree.get_children())).pack(side='left')
        ttk.Button(actions,text='补充主体税号',command=self.tax_number).pack(side='left',padx=8)
        self.status=ttk.StringVar()
        ttk.Label(parent,textvariable=self.status).pack(anchor='w')
        self.table=table(parent,[('id','编号',65),('label','记录',220),('date','日期',105),
            ('project','项目',220),('amount','金额（元）',110),('entity','当前主体',230),('suggest','项目默认建议',230)])
        self.tree=self.table.tree
        self.refresh()

    def refresh(self):
        self.rows=entities.list_records(self.kinds[self.kind.get()],pending_only=self.pending.get(),keyword=self.keyword.get().strip())
        self.table.refresh(self.rows,lambda r:(str(r['id']),(
            r['id'],r['label'],r['date'],r['projects'],
            '' if r['amount_minor'] is None else money(r['amount_minor']),
            self.names.get(r['entity_id'],'待确认'),
            self.names.get(r['suggested_entity_id'],'无明确建议'))))
        self.status.set(f'当前显示 {len(self.rows)} 条。项目默认建议仅供核对，不代表已确认。')

    def assign(self):
        ids=self.tree.selection()
        if not ids:
            return messagebox.showinfo('请选择','请先选中要确认的记录',parent=self.parent)
        target=self.entity.get()
        if not messagebox.askyesno('确认批量归属',f'将选中的{len(ids)}条“{self.kind.get()}”归属到：\n{target}\n\n历史收入、成本金额和回款匹配不会改变。是否继续？',parent=self.parent):
            return
        try:
            entities.assign_records(self.kinds[self.kind.get()],ids,self.entities[target]['id'])
            self.refresh()
        except Exception as error:
            messagebox.showerror('未保存',str(error),parent=self.parent)

    def tax_number(self):
        entity=self.entities[self.entity.get()]
        value=simpledialog.askstring('主体税号',entity['name'],initialvalue=entity['tax_number'],parent=self.parent)
        if value is None:
            return
        try:
            entities.update_tax_number(entity['id'],value)
            entity['tax_number']=value.strip().upper()
        except Exception as error:
            messagebox.showerror('未保存',str(error),parent=self.parent)


class InputInvoicePage:
    def __init__(self,parent):
        self.parent=parent
        self.entities={r['name']:r for r in entities.list_entities()}
        self.entity_names={r['id']:r['name'] for r in self.entities.values()}
        ttk.Label(parent,text='进项票只关联已有采购和费用，不再增加成本。下方主体筛选独立于页面顶部项目筛选。',wraplength=900).pack(anchor='w')
        ttk.Label(parent,text='抵扣状态由会计确认。现有经营利润仍沿用原口径，本页税额不是应缴税额。',style='PageSub.TLabel').pack(anchor='w',pady=4)
        bar=ttk.Frame(parent)
        bar.pack(fill='x',pady=6)
        self.entity=ttk.StringVar(value='全部主体')
        combo=ttk.Combobox(bar,textvariable=self.entity,values=['全部主体',*self.entities],state='readonly',width=35)
        combo.pack(side='left')
        combo.bind('<<ComboboxSelected>>',lambda e:self.refresh())
        for label,command in [('上传PDF识别',lambda:self.edit(pdf=True)),('手动登记',self.edit),('刷新',self.refresh)]:
            ttk.Button(bar,text=label,command=command).pack(side='left',padx=4)
        self.show_void=ttk.BooleanVar(value=False)
        ttk.Checkbutton(parent,text='显示已作废（不计入汇总）',variable=self.show_void,command=self.refresh).pack(anchor='w')
        self.summary=ttk.StringVar()
        ttk.Label(parent,textvariable=self.summary,wraplength=960).pack(anchor='w')
        actions=ttk.Frame(parent)
        actions.pack(fill='x',pady=6)
        commands=[('关联采购 / 费用',self.link),('修改票面',self.edit_selected),('抵扣状态',self.deduction),
                  ('查看PDF',self.open_pdf),('补传PDF',self.attach_pdf),('作废',self.void),('缺票采购 / 费用',self.missing)]
        for index,(label,command) in enumerate(commands):
            ttk.Button(actions,text=label,command=command).grid(row=index//4,column=index%4,sticky='w',padx=(0,6),pady=3)
        self.table=table(parent,[('no','发票号码',180),('date','开票日期',105),('entity','购买主体',230),('seller','供应商',200),
            ('gross','价税合计',110),('tax','税额',100),('linked','已关联成本',110),('remaining','待关联金额',110),
            ('deduction','抵扣状态',110),('pdf','附件',80),('status','状态',70),('warning','关联检查',220)])
        self.tree=self.table.tree
        self.refresh()

    def refresh(self):
        entity=self.entities.get(self.entity.get(),{}).get('id')
        rows=invoices.list_invoices(entity,include_void=self.show_void.get())
        self.rows={r['id']:r for r in rows}
        self.table.refresh(rows,lambda r:(str(r['id']),(
            r['invoice_no'],r['invoice_date'],r['entity_name'],r['seller_name'],
                money(r['gross_minor']),money(r['tax_minor']),money(r['linked_minor']),money(r['remaining_minor']),
                invoices.DEDUCTIONS[r['deduction_status']],r['attachment_status'],
                '有效' if r['status']=='active' else '已作废',r['warning'] or '无异常')))
        rows=[r for r in rows if r['status']=='active']
        eligible=sum(r['tax_minor'] for r in rows if r['deduction_status'] in ('eligible','deducted'))
        pending=sum(r['tax_minor'] for r in rows if r['deduction_status']=='pending')
        self.summary.set(f"有效{len(rows)}张  价税合计{money(sum(r['gross_minor'] for r in rows))}元  会计确认可抵扣/已抵扣税额{money(eligible)}元  待确认税额{money(pending)}元")

    def selected(self):
        ids=self.tree.selection()
        if len(ids)!=1:
            messagebox.showinfo('请选择','请选择一张进项票',parent=self.parent)
            return None
        return self.rows[int(ids[0])]

    def edit_selected(self):
        row=self.selected()
        if row:
            self.edit(existing=row)

    def edit(self,pdf=False,existing=None):
        recognition=None
        if pdf:
            path=filedialog.askopenfilename(parent=self.parent,title='选择进项发票PDF',filetypes=[('PDF','*.pdf')])
            if not path:
                return
            try:
                recognition=invoices.recognize_pdf(path)
            except Exception as error:
                return messagebox.showerror('识别失败',str(error),parent=self.parent)
        suppliers=list_suppliers()
        supplier_map={f"{r['id']} · {r['name']}":r for r in suppliers}
        dialog=ttk.Toplevel(self.parent)
        dialog.title('核对进项发票')
        body,footer=build_form_dialog(dialog,self.parent,780,690)
        initial=dict(recognition or {})
        if existing:
            initial.update(existing,amount=str(Decimal(existing['gross_minor'])/100),
                           net_amount=str(Decimal(existing['net_minor'])/100),tax_amount=str(Decimal(existing['tax_minor'])/100))
        fields={}
        selectors={}
        entity_value=self.entity_names.get(initial.get('entity_id'),'')
        if not entity_value and initial.get('buyer_name') in self.entities:
            entity_value=initial['buyer_name']
        choices=[('entity','购买主体',list(self.entities),entity_value),
                 ('supplier','供应商（可输入关键词）',list(supplier_map),next((k for k,r in supplier_map.items() if r['id']==initial.get('supplier_id') or r['name']==initial.get('seller_name')), ''))]
        for key,label,options,value in choices:
            ttk.Label(body,text=label).pack(anchor='w',pady=(6,2))
            fields[key]=ttk.StringVar(value=value)
            combo=ttk.Combobox(body,textvariable=fields[key],values=options,state='normal' if key=='supplier' else 'readonly',width=60)
            selectors[key]=combo
            combo.pack(fill='x')
            if key=='supplier':
                combo.bind('<KeyRelease>',lambda e,c=combo,opts=options:c.configure(values=[v for v in opts if fields['supplier'].get().casefold() in v.casefold()]))
        labels=[('invoice_no','发票号码'),('invoice_date','开票日期'),('buyer_name','票面购买方全称'),('seller_name','票面销售方全称'),
                ('buyer_tax_number','购买方税号'),('seller_tax_number','销售方税号'),('net_amount','未税金额（元）'),
                ('tax_amount','税额（元）'),('amount','价税合计（元）'),('tax_rate_label','税率说明，例如13%或多税率'),('notes','备注')]
        for key,label in labels:
            ttk.Label(body,text=label).pack(anchor='w',pady=(6,2))
            fields[key]=ttk.StringVar(value=initial.get(key) or (date.today().isoformat() if key=='invoice_date' else ''))
            widget=DatePicker(body,textvariable=fields[key]) if key=='invoice_date' else ttk.Entry(body,textvariable=fields[key])
            widget.pack(anchor='w',fill=None if key=='invoice_date' else 'x')
        auto_names={}
        def fill_name(choice, field, name):
            current=fields[field].get().strip()
            if not current or current==auto_names.get(choice):
                fields[field].set(name)
                auto_names[choice]=name
        selectors['entity'].bind('<<ComboboxSelected>>',lambda e:fill_name('entity','buyer_name',fields['entity'].get()))
        selectors['supplier'].bind('<<ComboboxSelected>>',lambda e:fill_name('supplier','seller_name',supplier_map.get(fields['supplier'].get(),{}).get('name','')))
        if recognition:
            ttk.Label(body,text='\n'.join(recognition['warnings']),wraplength=660).pack(anchor='w',pady=8)
        if existing and (existing.get('file_path') or existing.get('file_sha256')):
            ttk.Label(body,text='已保存原始PDF，票号、日期、购销双方、金额及税率不可直接修改；仍可补充备注或调整成本关联。',wraplength=660).pack(anchor='w',pady=8)
        checked=ttk.BooleanVar(value=False)
        ttk.Checkbutton(body,text='我已核对原票抬头、号码、金额与税额',variable=checked).pack(anchor='w',pady=8)
        def save():
            try:
                if not checked.get():
                    raise ValueError('请先核对原票并勾选确认')
                data={k:v.get().strip() for k,v in fields.items()}
                if data['entity'] not in self.entities or data['supplier'] not in supplier_map:
                    raise ValueError('请选择购买主体和供应商候选项')
                data['entity_id']=self.entities[data.pop('entity')]['id']
                data['supplier_id']=supplier_map[data.pop('supplier')]['id']
                data['deduction_status']=existing['deduction_status'] if existing else 'pending'
                data['deduction_period']=existing['deduction_period'] if existing else ''
                if existing:
                    invoices.update_invoice(existing['id'],data)
                else:
                    invoices.create_invoice(data,recognition=recognition)
                dialog.destroy()
                self.refresh()
            except Exception as error:
                messagebox.showerror('未保存',str(error),parent=dialog)
        add_form_actions(footer,cancel_command=dialog.destroy,primary_text='确认保存',primary_command=save)

    def link(self):
        invoice=self.selected()
        if not invoice:
            return
        dialog=ttk.Toplevel(self.parent)
        dialog.title('关联已有成本')
        body,footer=build_form_dialog(dialog,self.parent,1000,640)
        ttk.Label(body,text=f"发票{invoice['invoice_no']}  合计{money(invoice['gross_minor'])}元。双击候选记录设置关联金额；不产生新成本。",wraplength=860).pack(anchor='w')
        candidates=invoices.cost_candidates(invoice['entity_id'],invoice['supplier_id'])+invoices.cost_candidates(invoice['entity_id'],invoice['supplier_id'],'cost_entries')
        amounts={}
        for link in invoice['links']:
            key=('purchase_orders',link['purchase_order_id']) if link['purchase_order_id'] else ('cost_entries',link['cost_entry_id'])
            amounts[key]=link['amount_minor']
        listing_table=table(body,[('type','类别',70),('no','记录',175),('name','单位',240),('gross','成本金额',100),('available','可关联含本票',120),('amount','本票关联金额',120)])
        listing=listing_table.tree
        mapping={}
        def redraw():
            mapping.clear()
            def row_values(item):
                index,r=item
                key=(r['kind'],r['id'])
                mapping[str(index)]=key
                existing=sum(l['amount_minor'] for l in invoice['links'] if (l['purchase_order_id'] if r['kind']=='purchase_orders' else l['cost_entry_id'])==r['id'])
                return str(index),('采购' if r['kind']=='purchase_orders' else '费用',r['label'],r['counterparty'],money(r['amount_minor']),money(r['remaining_minor']+existing),money(amounts.get(key,0)))
            listing_table.refresh(enumerate(candidates),row_values)
        def choose(event=None):
            selected=listing.selection()
            if len(selected)!=1:
                return
            key=mapping[selected[0]]
            value=simpledialog.askstring('关联金额','输入本票关联金额（元），填0取消关联',initialvalue=str(Decimal(amounts.get(key,0))/100),parent=dialog)
            if value is not None:
                try:
                    amounts[key]=invoices.money(value)
                    redraw()
                except ValueError as error:
                    messagebox.showerror('金额错误',str(error),parent=dialog)
        listing.bind('<Double-1>',choose)
        ttk.Button(body,text='设置选中记录的关联金额',command=choose).pack(anchor='w')
        def clear_links():
            if messagebox.askyesno('清空本票关联', '清空当前编辑中的全部关联，包括已失效的旧关联。点击保存后生效，不删除成本。', parent=dialog):
                amounts.clear()
                redraw()
        ttk.Button(body,text='清空本票全部关联（含失效关联）',command=clear_links).pack(anchor='w',pady=4)
        ttk.Label(body,text='没有候选？先在“经营主体”页确认采购/费用归属，并核对供应商或费用往来单位。',wraplength=860).pack(anchor='w',pady=8)
        redraw()
        def save():
            try:
                links=[{'kind':k[0],'record_id':k[1],'amount':str(Decimal(v)/100)} for k,v in amounts.items() if v]
                invoices.update_links(invoice['id'],links)
                dialog.destroy()
                self.refresh()
            except Exception as error:
                messagebox.showerror('未保存',str(error),parent=dialog)
        add_form_actions(footer,cancel_command=dialog.destroy,primary_text='保存关联',primary_command=save)

    def deduction(self):
        row=self.selected()
        if not row:
            return
        dialog=ttk.Toplevel(self.parent)
        dialog.title('会计确认抵扣状态')
        body,footer=build_form_dialog(dialog,self.parent,560,380)
        status=ttk.StringVar(value=invoices.DEDUCTIONS[row['deduction_status']])
        ttk.Label(body,text='请根据会计确认设置，不根据“有发票”自动推断').pack(anchor='w')
        ttk.Combobox(body,textvariable=status,values=list(invoices.DEDUCTIONS.values()),state='readonly').pack(fill='x',pady=8)
        ttk.Label(body,text='抵扣月份（仅已抵扣填写 YYYY-MM）').pack(anchor='w')
        period=ttk.StringVar(value=row['deduction_period'] or '')
        ttk.Entry(body,textvariable=period).pack(fill='x',pady=6)
        ttk.Label(body,text='会计确认依据 / 转出说明').pack(anchor='w')
        reason=ttk.StringVar()
        ttk.Entry(body,textvariable=reason).pack(fill='x',pady=6)
        def save():
            try:
                code=next(k for k,v in invoices.DEDUCTIONS.items() if v==status.get())
                invoices.set_deduction(row['id'],code,period.get().strip(),reason.get())
                dialog.destroy()
                self.refresh()
            except Exception as error:
                messagebox.showerror('未保存',str(error),parent=dialog)
        add_form_actions(footer,cancel_command=dialog.destroy,primary_text='确认状态',primary_command=save)

    def open_pdf(self):
        row=self.selected()
        if not row:
            return
        from services.attachment_service import attachment_path
        if not row['file_path'] or not attachment_path(row['file_path']).is_file():
            return messagebox.showinfo('附件','尚未上传或文件缺失',parent=self.parent)
        try:
            os.startfile(attachment_path(row['file_path']))
        except OSError as error:
            messagebox.showerror('无法打开PDF',str(error),parent=self.parent)

    def attach_pdf(self):
        row=self.selected()
        if not row:
            return
        path=filedialog.askopenfilename(parent=self.parent,title='补传同一张发票的PDF',filetypes=[('PDF','*.pdf')])
        if not path:
            return
        if not messagebox.askyesno('核对原票','请确认该PDF属于选中的发票，且购销双方与票面金额一致。继续上传？',parent=self.parent):
            return
        try:
            invoices.attach_pdf(row['id'],path)
            self.refresh()
        except Exception as error:
            messagebox.showerror('未上传',str(error),parent=self.parent)

    def void(self):
        row=self.selected()
        if not row:
            return
        reason=simpledialog.askstring('作废进项票','请填写原因。保留原记录及附件，不删除采购成本。',parent=self.parent)
        if not reason:
            return
        try:
            invoices.void_invoice(row['id'],reason)
            self.refresh()
        except Exception as error:
            messagebox.showerror('未作废',str(error),parent=self.parent)

    def missing(self):
        entity=self.entities.get(self.entity.get())
        if not entity:
            return messagebox.showinfo('选择主体','请先选择一家经营主体',parent=self.parent)
        dialog=ttk.Toplevel(self.parent)
        dialog.title('尚未关联进项票的成本')
        body,footer=build_form_dialog(dialog,self.parent,930,600)
        ttk.Label(body,text='列出已确认主体、尚未关联进项票的采购和直接费用；不代表每笔都必须开票或能抵扣。未确认主体请先处理归属，未上传发票不扣除原成本。',wraplength=820).pack(anchor='w')
        listing=table(body,[('type','类别',80),('label','记录',190),('party','单位',240),('amount','成本金额',110),('missing','尚未关联',110)])
        missing_rows=[]
        for kind in ('purchase_orders','cost_entries'):
            for r in invoices.cost_candidates(entity['id'],kind=kind):
                if r['remaining_minor']:
                    missing_rows.append((kind,r))
        listing.refresh(missing_rows,lambda item:(None,(
            '采购' if item[0]=='purchase_orders' else '费用',item[1]['label'],
            item[1]['counterparty'],money(item[1]['amount_minor']),
            money(item[1]['remaining_minor']))))
        ttk.Button(footer,text='关闭',command=dialog.destroy).pack(side='right')
