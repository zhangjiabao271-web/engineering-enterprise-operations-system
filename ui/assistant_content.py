"""Presentation-only answer parsing: never query data or recompute amounts."""

import re
import tkinter as tk
from tkinter.font import Font

import ttkbootstrap as ttk
from ttkbootstrap.constants import E, W, X

from ui.components import DataTable
from ui.scaling import scale_px
from ui.theme import COLORS, FONT_BODY, FONT_BODY_MEDIUM, FONT_SECTION, FONT_METRIC_SUB


def _cells(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith(r"\|"):
        line = line[:-1]
    values = [value.strip().replace(r"\|", "|") for value in re.split(r"(?<!\\)\|", line)]
    return [re.sub(r"\*\*(.+?)\*\*|`([^`]+)`", lambda match: match[1] or match[2], value)
            for value in values]


def _legacy_profit_blocks(text):
    """Upgrade the old local profit answer without touching stored messages."""
    lines = text.strip().splitlines()
    if len(lines) < 3 or not lines[0].startswith("已登记收支毛利分析"):
        return None
    heading = re.split(r"[｜|]", lines[0], maxsplit=1)
    metrics, notes = [], []
    for item in lines[1].split("；"):
        match = re.fullmatch(r"(确认收入|已登记经营成本|已登记毛利)[：:]\s*(-?¥-?[\d,.]+|未知)", item.strip())
        if match:
            metrics.append(match.groups())
        else:
            notes.append(item)
    if not metrics:
        return None
    headers, rows, month_notes = None, [], []
    for line in lines[2:]:
        if line.startswith(("口径：", "注意：", "分组仅展示")):
            notes.append(line)
            continue
        if "：" not in line:
            return None
        name, detail = line.split("：", 1)
        share = re.search(r"（([^（）]+占比)(-?\d+(?:\.\d+)?)%）$", detail)
        if share:
            detail = detail[:share.start()]
        fields = []
        for item in detail.split("；"):
            match = re.fullmatch(
                r"(确认收入|已登记经营成本|已登记毛利|月均毛利)\s+(-?¥-?[\d,.]+|未知)(?:（(\d+)个自然月）)?",
                item.strip(),
            )
            if match:
                label, value, months = match.groups()
                fields.append((label, value))
                if months:
                    month_notes.append(f"月均毛利按{months}个自然月计算，非实际施工月份。")
            elif item.strip().startswith("月均毛利无法可靠计算"):
                fields.append(("月均毛利", item.strip().removeprefix("月均毛利")))
            else:
                return None
        if share:
            fields.append((share[1], share[2] + "%"))
        fields.sort(key=lambda field: {
            "已登记毛利": 0, "确认收入": 1, "已登记经营成本": 2, "月均毛利": 3,
        }.get(field[0], 4))
        row_headers = ["项目"] + [label for label, _value in fields]
        if headers is not None and headers != row_headers:
            return None
        headers = row_headers
        rows.append([name] + [value for _label, value in fields])
    if not rows:
        return None
    blocks = [{"type": "heading", "text": heading[0]},
              {"type": "paragraph", "text": heading[1] if len(heading) > 1 else ""},
              {"type": "metrics", "items": metrics},
              {"type": "heading", "text": "项目对比"},
              {"type": "table", "headers": headers, "rows": rows}]
    if notes or month_notes:
        blocks.append({"type": "heading", "text": "口径与注意事项"})
        blocks.extend({"type": "paragraph", "text": note}
                      for note in dict.fromkeys(notes + month_notes) if note)
    return blocks


def parse_answer_blocks(text):
    """Recognize basic Markdown and the legacy local report; leave unknown text intact."""
    text = str(text or "")
    legacy = _legacy_profit_blocks(text)
    if legacy is not None:
        return legacy
    lines = text.splitlines()
    blocks, paragraph = [], []

    def flush():
        if paragraph:
            content = "\n".join(paragraph)
            report = _legacy_profit_blocks(content)
            if report is not None:
                blocks.extend(report)
            else:
                blocks.append({"type": "paragraph", "text": content})
            paragraph.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        if line.strip().startswith("```"):
            flush()
            index += 1
            code = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index])
                index += 1
            blocks.append({"type": "code", "text": "\n".join(code)})
        elif "|" in line and index + 1 < len(lines):
            headers, separator = _cells(line), _cells(lines[index + 1])
            if len(headers) == len(separator) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator):
                flush()
                rows = []
                index += 2
                while index < len(lines) and "|" in lines[index]:
                    row = _cells(lines[index])
                    if len(row) != len(headers):
                        break
                    rows.append(row)
                    index += 1
                blocks.append({"type": "table", "headers": headers, "rows": rows,
                               "alignments": separator})
                continue
            paragraph.append(line)
        elif not line.strip():
            flush()
        else:
            heading = re.fullmatch(r"#{1,6}\s+(.+)", line.strip())
            if heading:
                flush()
                blocks.append({"type": "heading", "text": heading[1]})
            elif re.match(r"^\s*[-*+]\s+", line):
                flush()
                blocks.append({"type": "paragraph", "text": re.sub(r"^\s*[-*+]\s+", "• ", line)})
            else:
                paragraph.append(line)
        index += 1
    flush()
    return blocks


def answer_as_markdown(text):
    """Copy what is displayed, including an upgraded historical report."""
    parts = []
    for block in parse_answer_blocks(text):
        kind = block["type"]
        if kind == "table":
            def row(values):
                return "| " + " | ".join(str(value).replace("|", r"\|") for value in values) + " |"
            parts.append("\n".join([row(block["headers"]), row(["---"] * len(block["headers"]))]
                                   + [row(values) for values in block["rows"]]))
        elif kind == "metrics":
            parts.append("\n".join(f"- {label}：**{value}**" for label, value in block["items"]))
        elif kind == "heading":
            parts.append("## " + block["text"])
        elif kind == "code":
            parts.append("```\n" + block["text"] + "\n```")
        else:
            parts.append(block["text"])
    return "\n\n".join(parts)


class AnswerText(tk.Text):
    """Selectable text; ordinary paragraphs grow, very long ones stay bounded."""

    MAX_AUTO_CHARACTERS = 1200
    MAX_AUTO_LINES = 24

    def __init__(self, parent, text, *, heading=False, code=False):
        self.bounded = len(text) > self.MAX_AUTO_CHARACTERS or text.count("\n") >= self.MAX_AUTO_LINES
        super().__init__(parent, width=1, height=12 if self.bounded else 1,
                         wrap="char" if self.bounded else "word", relief="flat",
                         borderwidth=0, highlightthickness=0, padx=0, pady=2,
                         background=COLORS["surface"], foreground=COLORS["text"],
                         selectbackground=COLORS["primary_soft"], selectforeground=COLORS["text"],
                         font=FONT_SECTION if heading else FONT_BODY, cursor="arrow", takefocus=True)
        # ttkbootstrap applies defaults while creating classic Tk widgets.
        self.configure(relief="flat", borderwidth=0, highlightthickness=0)
        self.tag_configure("strong", font=FONT_SECTION if heading else FONT_BODY_MEDIUM)
        self.tag_configure("code", font=("Consolas", 10), background=COLORS["surface_muted"])
        self._resize_after = None
        self._measured_width = None
        if code:
            self.insert("end", text, "code")
        else:
            position = 0
            for match in re.finditer(r"\*\*(.+?)\*\*|`([^`]+)`", text):
                self.insert("end", text[position:match.start()])
                self.insert("end", match[1] or match[2], "strong" if match[1] is not None else "code")
                position = match.end()
            self.insert("end", text[position:])
        self.configure(state="disabled")
        self.bind("<Configure>", self._queue_resize)
        self.bind("<Destroy>", self._cancel_resize, add="+")

    def _queue_resize(self, _event):
        if self.bounded:
            return
        width = self.winfo_width()
        if width < scale_px(self, 40) or width == self._measured_width:
            return
        if self._resize_after is not None:
            self.after_cancel(self._resize_after)
        # Let geometry settle first. Counting display lines at the initial
        # one-pixel width can stall Tk; an idle callback runs too early.
        self._resize_after = self.after(25, self._fit_height)

    def _cancel_resize(self, event):
        if event.widget is self and self._resize_after is not None:
            self.after_cancel(self._resize_after)
            self._resize_after = None

    def _fit_height(self):
        self._resize_after = None
        if self.bounded or not self.winfo_exists() or not self.winfo_ismapped():
            return
        width = self.winfo_width()
        if width < scale_px(self, 40) or width == self._measured_width:
            return
        count = self.count("1.0", "end-1c", "displaylines")
        self._measured_width = width
        height = max(1, (count[0] if count else 0) + 1)
        if int(self.cget("height")) != height:
            self.configure(height=height)


class MetricStrip(ttk.Frame):
    def __init__(self, parent, items):
        super().__init__(parent, style="ChatAssistant.TFrame")
        self._cards, self._columns = [], 0
        value_font = Font(master=self, font=FONT_METRIC_SUB)
        self._minimum_card_width = max(
            scale_px(self, 180),
            max((value_font.measure(value) for _label, value in items), default=0) + scale_px(self, 20),
        )
        for label, value in items:
            card = ttk.Frame(self, style="ChatAssistant.TFrame", padding=(0, 5, 12, 8))
            ttk.Label(card, text=label, style="CardText.TLabel").pack(anchor=W)
            value_label = ttk.Label(card, text=value, font=FONT_METRIC_SUB,
                                    foreground=COLORS["text"], background=COLORS["surface"])
            value_label.pack(anchor=W, pady=(5, 0))
            card.bind("<Configure>", lambda event, label=value_label: self._wrap_value(event, label))
            self._cards.append(card)
        self.bind("<Configure>", self._reflow)

    @staticmethod
    def _wrap_value(event, label):
        wraplength = max(100, event.width - scale_px(label, 12))
        if int(label.cget("wraplength") or 0) != wraplength:
            label.configure(wraplength=wraplength)

    def _reflow(self, event):
        columns = max(1, min(len(self._cards), event.width // self._minimum_card_width))
        if columns == self._columns:
            return
        for column in range(max(self._columns, columns)):
            self.columnconfigure(column, weight=1 if column < columns else 0)
        for index, card in enumerate(self._cards):
            card.grid(row=index // columns, column=index % columns, sticky="ew")
        self._columns = columns


def render_answer_content(parent, text):
    """Render an answer using native controls; never execute HTML, links or code."""
    tables = []
    for block in parse_answer_blocks(text):
        kind = block["type"]
        if kind == "metrics":
            MetricStrip(parent, block["items"]).pack(fill=X, pady=(4, 10))
        elif kind == "table":
            specs = []
            for index, header in enumerate(block["headers"]):
                values = [str(row[index]) for row in block["rows"]]
                alignment = (block.get("alignments") or [""] * len(block["headers"]))[index]
                numeric = alignment.endswith(":") or bool(values and all(
                    re.fullmatch(r"[-+]?¥?[-+]?[\d,.]+%?|未知|—", value) for value in values
                ))
                width = 145 if numeric else 280
                specs.append((f"column_{index}", header, width, E if numeric else W))
            table = DataTable(parent, tuple(specs), height=min(8, max(1, len(block["rows"]))),
                              padding=0, horizontal=True, stretch=(), pack_fill=X, pack_expand=False)
            table.pack_configure(pady=(5, 8))
            table.refresh(block["rows"], lambda values: (None, values))
            if len(block["rows"]) > 8:
                def scroll_rows(event, tree=table.tree):
                    tree.yview_scroll(-int(event.delta / 120), "units")
                    return "break"
                table.tree.bind("<MouseWheel>", scroll_rows)
            tables.append(table)
            ttk.Label(parent, text=f"共 {len(block['rows'])} 行 · 点击表头排序 · 可横向滚动查看",
                      style="CardText.TLabel").pack(anchor=W, pady=(0, 10))
        else:
            body = ttk.Frame(parent, style="ChatAssistant.TFrame")
            body.pack(fill=X, pady=(8, 4) if kind == "heading" else (0, 8))
            widget = AnswerText(body, block["text"], heading=kind == "heading", code=kind == "code")
            if widget.bounded:
                scrollbar = ttk.Scrollbar(body, command=widget.yview)
                scrollbar.pack(side="right", fill="y")
                widget.configure(yscrollcommand=scrollbar.set)
                def scroll_text(event, text_widget=widget):
                    text_widget.yview_scroll(-int(event.delta / 120), "units")
                    return "break"
                widget.bind("<MouseWheel>", scroll_text)
            widget.pack(fill=X)
    return tables
