"""Rounded native ttk surfaces and a consistent set of sidebar outline icons."""

from PIL import Image, ImageDraw, ImageTk

from ui.scaling import scale_px
from ui.theme import COLORS


def install_desktop_style(root):
    style = root.style
    images = []
    root._desktop_style_images = images

    def tile(fill, outline=None, radius=8, outside=None):
        size = scale_px(root, 32)
        factor = 3
        image = Image.new("RGBA", (size * factor, size * factor), outside or (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (factor, factor, (size - 1) * factor, (size - 1) * factor),
            radius=scale_px(root, radius) * factor,
            fill=fill, outline=outline, width=scale_px(root, 1) * factor,
        )
        photo = ImageTk.PhotoImage(image.resize((size, size), Image.Resampling.LANCZOS), master=root)
        images.append(photo)
        return photo

    def surface(name, fill, *, outline=None, outside=None, radius=10):
        element = f"Desktop.{name}.surface"
        image = tile(fill, outline, radius, outside)
        style.element_create(element, "image", image, border=scale_px(root, 12), sticky="nsew")
        style.layout(name, [(element, {"sticky": "nsew"})])

    surface("Surface.TFrame", COLORS["surface"], outline=COLORS["border"])
    surface("Kpi.TFrame", COLORS["surface_muted"], outside=COLORS["surface"])
    surface("Toolbar.TFrame", COLORS["surface"], outline=COLORS["border"])
    surface("LedgerSpine.TFrame", COLORS["surface"], outline=COLORS["border"])

    def button(name, fill, hover, pressed, foreground, *, outline=None):
        element = f"Desktop.{name}.button"
        backdrop = COLORS["sidebar"] if name.startswith("Nav") else COLORS["surface"]
        normal = tile(fill, outline, outside=backdrop)
        active = tile(hover, outline, outside=backdrop)
        down = tile(pressed, outline, outside=backdrop)
        focus_color = COLORS["text"] if name in ("primary.TButton", "NavActive.TButton") else COLORS["primary"]
        focus = tile(fill, focus_color, outside=backdrop)
        disabled = tile(COLORS["surface_muted"], COLORS["border"], outside=backdrop)
        style.element_create(
            element, "image", normal,
            ("disabled", disabled), ("pressed", down), ("focus", focus), ("active", active),
            border=scale_px(root, 9), padding=0, sticky="nsew",
        )
        style.layout(name, [(element, {"sticky": "nsew", "children": [
            ("Button.padding", {"sticky": "nsew", "children": [
                ("Button.label", {"sticky": "nsew"}),
            ]}),
        ]})])
        style.configure(name, foreground=foreground, background=backdrop)
        style.map(name, foreground=[("disabled", "#929299"), ("!disabled", foreground)])
        style.map(name, background=[("!disabled", backdrop), ("disabled", backdrop)])

    button("primary.TButton", COLORS["primary"], "#0072E3", COLORS["primary_hover"], "#FFFFFF")
    button_specs = [
        ("TButton", COLORS["text"]),
        ("secondary.TButton", COLORS["text"]),
        ("success.TButton", COLORS["accent"]),
        ("info.TButton", COLORS["primary"]),
        ("warning.TButton", COLORS["warning"]),
        ("danger.TButton", COLORS["danger"]),
        ("Outline.TButton", COLORS["text_muted"]),
    ]
    for color, token in (
        ("primary", "primary"), ("secondary", "text"), ("success", "accent"),
        ("info", "primary"), ("warning", "warning"), ("danger", "danger"),
    ):
        button_specs.append((f"{color}.Outline.TButton", COLORS[token]))
    for name, color in button_specs:
        button(name, COLORS["surface"], COLORS["surface_muted"], "#E8E8ED", color, outline=COLORS["border"])
    button("Nav.TButton", COLORS["sidebar"], COLORS["sidebar_hover"], "#DCDCE3", COLORS["sidebar_text"])
    button("NavActive.TButton", COLORS["primary"], "#0072E3", COLORS["primary_hover"], "#FFFFFF")
    for name in ("Link.TButton", "primary.Link.TButton"):
        style.layout(name, [("Button.padding", {"sticky": "nsew", "children": [
            ("Button.label", {"sticky": "nsew"}),
        ]})])

    # Preserve native editable areas and keyboard handling inside rounded fields.
    field = "Desktop.Entry.field"
    style.element_create(
        field, "image", tile(COLORS["surface"], COLORS["border"]),
        ("focus", tile(COLORS["surface"], COLORS["primary"])),
        ("disabled", tile(COLORS["surface_muted"], COLORS["border"])),
        border=scale_px(root, 7), padding=0, sticky="nsew",
    )
    style.layout("TEntry", [(field, {"sticky": "nsew", "children": [
        ("Entry.padding", {"sticky": "nsew", "children": [("Entry.textarea", {"sticky": "nsew"})]}),
    ]})])
    style.layout("TCombobox", [(field, {"sticky": "nsew", "children": [
        ("Combobox.downarrow", {"side": "right", "sticky": ""}),
        ("Combobox.padding", {"sticky": "nsew", "children": [("Combobox.textarea", {"sticky": "nsew"})]}),
    ]})])
    for name in ("TEntry", "TCombobox"):
        style.configure(name, padding=(scale_px(root, 8), scale_px(root, 5)))

    tab = "Desktop.Notebook.tab"
    style.element_create(
        tab, "image", tile(COLORS["surface_muted"]),
        ("selected", tile(COLORS["surface"], COLORS["border"])),
        ("active", tile("#EAEAF0")),
        border=scale_px(root, 9), padding=0, sticky="nsew",
    )
    for name in ("TNotebook.Tab", "primary.TNotebook.Tab"):
        style.configure(name, padding=(scale_px(root, 12), scale_px(root, 6)))
        style.map(
            name,
            background=[("!disabled", COLORS["surface"])],
            padding=[("selected", (scale_px(root, 12), scale_px(root, 6))),
                     ("!selected", (scale_px(root, 12), scale_px(root, 6)))],
        )
        style.layout(name, [(tab, {"sticky": "nsew", "children": [
            ("Notebook.padding", {"sticky": "nsew", "children": [
                ("Notebook.focus", {"sticky": "nsew", "children": [
                    ("Notebook.label", {"sticky": "nsew"}),
                ]}),
            ]}),
        ]})])


def navigation_icon(root, key, color):
    """Draw small monochrome icons at display resolution, with a shared stroke."""
    factor = 4
    image = Image.new("RGBA", (32 * factor, 24 * factor))
    draw = ImageDraw.Draw(image)

    def line(points):
        draw.line([(x * factor, y * factor) for x, y in points], fill=color, width=6, joint="curve")

    def box(bounds, radius=2):
        draw.rounded_rectangle(tuple(v * factor for v in bounds), radius=radius * factor, outline=color, width=6)

    def oval(bounds):
        draw.ellipse(tuple(v * factor for v in bounds), outline=color, width=6)

    if key == "home":
        for x, y in ((3, 3), (14, 3), (3, 14), (14, 14)):
            box((x, y, x + 7, y + 7), 1)
    elif key in ("workspace", "project"):
        line([(3, 20), (3, 7), (10, 7), (12, 10), (21, 10), (21, 20), (3, 20)])
    elif key in ("profit", "compare"):
        line([(3, 3), (3, 21), (22, 21)])
        line([(7, 17), (7, 12)])
        line([(13, 17), (13, 7)])
        line([(19, 17), (19, 4)])
    elif key in ("contract", "construction"):
        box((5, 2, 19, 22))
        for y in (8, 12, 16):
            line([(9, y), (15, y)])
    elif key in ("finance", "import_export"):
        line([(3, 8), (21, 8), (17, 4)])
        line([(21, 16), (3, 16), (7, 20)])
    elif key == "funds":
        box((3, 5, 21, 20))
        box((14, 10, 22, 16), 1)
    elif key == "cost":
        oval((3, 3, 21, 21))
        line([(12, 3), (12, 12), (21, 12)])
    elif key == "purchase":
        box((4, 8, 20, 21))
        line([(8, 9), (8, 3), (16, 3), (16, 9)])
    elif key == "workday":
        box((3, 5, 21, 21))
        line([(3, 10), (21, 10)])
        line([(8, 2), (8, 7)])
        line([(16, 2), (16, 7)])
        line([(8, 15), (11, 18), (17, 13)])
    elif key in ("supplier", "customer"):
        oval((8, 3, 16, 11))
        line([(4, 21), (5, 16), (9, 14), (15, 14), (19, 16), (20, 21)])
    elif key == "product":
        line([(12, 2), (22, 7), (22, 18), (12, 23), (2, 18), (2, 7), (12, 2)])
        line([(2, 7), (12, 12), (22, 7)])
        line([(12, 12), (12, 23)])
    elif key == "governance":
        line([(12, 2), (21, 6), (20, 15), (12, 22), (4, 15), (3, 6), (12, 2)])
        line([(7, 12), (11, 16), (17, 9)])
    elif key == "ai":
        line([(12, 2), (15, 9), (22, 12), (15, 15), (12, 22), (9, 15), (2, 12), (9, 9), (12, 2)])
    return ImageTk.PhotoImage(
        image.resize((scale_px(root, 25), scale_px(root, 19)), Image.Resampling.LANCZOS), master=root,
    )
