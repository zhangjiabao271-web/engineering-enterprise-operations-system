import os
from tkinter import filedialog, messagebox

import ttkbootstrap as ttk
from ttkbootstrap.constants import BOTH, CENTER, E, END, LEFT, RIGHT, W, X, Y

from services import attachment_service
from ui.theme import style_dialog


def open_attachment_manager(parent, entity_type, entity_id, title):
    dialog = ttk.Toplevel(parent)
    dialog.title(f"{title} · 附件")
    style_dialog(
        dialog, parent, 760, 520,
        resizable=True, min_width=620, min_height=420
    )

    header = ttk.Frame(dialog, padding=(18, 16, 18, 8))
    header.pack(fill=X)
    ttk.Label(
        header, text=f"{title}附件", style="PageTitle.TLabel"
    ).pack(anchor=W)
    ttk.Label(
        header,
        text="附件作废后保留文件和历史痕迹，不物理删除。",
        style="PageSub.TLabel",
    ).pack(anchor=W, pady=(3, 0))

    frame = ttk.Frame(dialog, style="Card.TFrame", padding=10)
    frame.pack(fill=BOTH, expand=True, padx=18, pady=8)
    tree = ttk.Treeview(
        frame,
        columns=("id", "name", "category", "description", "date"),
        show="headings",
        bootstyle="primary",
    )
    for key, label, width, anchor in (
        ("id", "ID", 45, CENTER),
        ("name", "文件名", 220, W),
        ("category", "分类", 100, CENTER),
        ("description", "说明", 230, W),
        ("date", "上传时间", 145, CENTER),
    ):
        tree.heading(key, text=label)
        tree.column(key, width=width, anchor=anchor)
    scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    tree.pack(side=LEFT, fill=BOTH, expand=True)
    scroll.pack(side=RIGHT, fill=Y)

    def refresh():
        tree.delete(*tree.get_children())
        for row in attachment_service.list_attachments(
            entity_type, entity_id
        ):
            tree.insert(
                "", END, iid=str(row["id"]),
                values=(
                    row["id"],
                    row["original_name"],
                    row["category"],
                    row["description"] or "",
                    row["created_at"][:19].replace("T", " "),
                ),
            )

    def add_file():
        path = filedialog.askopenfilename(
            parent=dialog,
            filetypes=[
                ("常用业务文件", "*.pdf *.doc *.docx *.xls *.xlsx *.jpg *.jpeg *.png"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        try:
            attachment_service.add_attachment(
                entity_type, entity_id, path
            )
        except Exception as error:
            messagebox.showwarning(
                "无法添加附件", str(error), parent=dialog
            )
            return
        refresh()

    def open_file():
        selected = tree.selection()
        if len(selected) != 1:
            messagebox.showwarning("提示", "请先选择一个附件", parent=dialog)
            return
        attachment_id = int(selected[0])
        row = next(
            item
            for item in attachment_service.list_attachments(
                entity_type, entity_id
            )
            if item["id"] == attachment_id
        )
        if not os.path.exists(row["absolute_path"]):
            messagebox.showwarning(
                "文件不存在", "附件记录存在，但本地文件已缺失。", parent=dialog
            )
            return
        os.startfile(row["absolute_path"])

    def void_selected():
        selected = tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先选择附件", parent=dialog)
            return
        if not messagebox.askyesno(
            "确认作废",
            f"确定作废选中的 {len(selected)} 个附件关联吗？",
            parent=dialog,
        ):
            return
        attachment_service.void_attachments(
            [int(item) for item in selected]
        )
        refresh()

    footer = ttk.Frame(dialog, padding=(18, 8, 18, 16))
    footer.pack(fill=X)
    ttk.Button(
        footer, text="添加文件", bootstyle="primary",
        command=add_file,
    ).pack(side=LEFT)
    ttk.Button(
        footer, text="打开文件", bootstyle="secondary-outline",
        command=open_file,
    ).pack(side=LEFT, padx=8)
    ttk.Button(
        footer, text="作废附件", bootstyle="danger-outline",
        command=void_selected,
    ).pack(side=LEFT)
    ttk.Button(
        footer, text="关闭", bootstyle="secondary",
        command=dialog.destroy,
    ).pack(side=RIGHT)
    refresh()
    return dialog
