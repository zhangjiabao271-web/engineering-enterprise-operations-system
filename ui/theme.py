"""Desktop adaptation of the persisted UI/UX Pro Max design system."""

from ui.scaling import (
    scale_px,
    scale_treeview_columns,
    window_frame_size,
    working_area,
)

COLORS = {
    "primary": "#986A3E",
    "primary_hover": "#805630",
    "primary_soft": "#F8F5F0",
    "accent": "#52715F",
    "accent_hover": "#405B4C",
    "accent_soft": "#F4F7F5",
    "background": "#FFFFFF",
    "surface": "#FFFFFF",
    "surface_muted": "#F7F8F5",
    "sidebar": "#FFFFFF",
    "sidebar_hover": "#F5F6F3",
    "sidebar_active": "#FFFFFF",
    "sidebar_text": "#5F625D",
    "text": "#20211F",
    "text_muted": "#6E706B",
    "border": "#D8DBD4",
    "danger": "#B34242",
    "danger_soft": "#FFFFFF",
    "warning": "#956820",
    "warning_soft": "#FFFFFF",
    "info_soft": "#FFFFFF",
    "cost_freight": "#6F7F8C",
    "cost_other": "#B8894C",
}

FONT_BODY = ("Microsoft YaHei UI", 9)
FONT_BODY_MEDIUM = ("Microsoft YaHei UI", 9, "bold")
FONT_CONTROL = ("Microsoft YaHei UI", 9)
FONT_TITLE = ("Microsoft YaHei UI", 18)
FONT_SECTION = ("Microsoft YaHei UI", 10, "bold")
FONT_METRIC = ("Bahnschrift SemiCondensed", 22, "bold")
FONT_METRIC_SUB = ("Bahnschrift SemiCondensed", 18, "bold")
FONT_DATA = ("Bahnschrift", 9)

# ---- Spacing tokens (report-style rhythm, all scale-safe) ----
SPACING = {
    "xs": 4,
    "sm": 8,
    "md": 12,
    "lg": 16,
    "xl": 24,
    "page_gap": 14,       # vertical rhythm between top-level sections
    "card_pad": 14,       # inside Card.TFrame
    "row_height": 34,     # Treeview row height
    "button_pad": (12, 8),
    "entry_pad": (8, 6),
    "table_head_pad": (8, 9),
}

# ---- Typography tokens (type scale for report-led interfaces) ----
TYPE = {
    "page_title": ("Microsoft YaHei UI", 18),
    "section": ("Microsoft YaHei UI", 10, "bold"),
    "body": ("Microsoft YaHei UI", 9),
    "body_medium": ("Microsoft YaHei UI", 9, "bold"),
    "control": ("Microsoft YaHei UI", 9),
    "label": ("Microsoft YaHei UI", 8),
    "label_bold": ("Microsoft YaHei UI", 8, "bold"),
    "metric": ("Bahnschrift SemiCondensed", 22, "bold"),
    "metric_sub": ("Bahnschrift SemiCondensed", 18, "bold"),
    "data": ("Bahnschrift", 9),
    "data_bold": ("Bahnschrift", 9, "bold"),
}


def configure_design_system(root):
    style = root.style
    root.configure(background=COLORS["background"])

    style.configure("TFrame", background=COLORS["background"])
    style.configure("TLabel", background=COLORS["background"], foreground=COLORS["text"], font=FONT_BODY)
    style.configure(
        "TButton", font=FONT_CONTROL, padding=SPACING["button_pad"],
        background=COLORS["surface"], foreground=COLORS["text"],
        bordercolor=COLORS["border"], focuscolor=COLORS["primary"],
    )
    style.configure(
        "TEntry", font=FONT_BODY, padding=SPACING["entry_pad"],
        fieldbackground=COLORS["surface"], foreground=COLORS["text"],
        bordercolor=COLORS["border"], focuscolor=COLORS["primary"],
    )
    style.configure(
        "TCombobox", font=FONT_BODY, padding=SPACING["entry_pad"],
        fieldbackground=COLORS["surface"], foreground=COLORS["text"],
        bordercolor=COLORS["border"], focuscolor=COLORS["primary"],
        arrowcolor=COLORS["text_muted"],
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", COLORS["surface"])],
        background=[("readonly", COLORS["surface"])],
        foreground=[("readonly", COLORS["text"])],
    )
    style.configure("TSeparator", background=COLORS["border"])
    style.configure("TLabelframe", background=COLORS["surface"], bordercolor=COLORS["border"], borderwidth=1)
    style.configure(
        "TLabelframe.Label",
        background=COLORS["surface"],
        foreground=COLORS["text"],
        font=FONT_SECTION,
    )

    style.configure("Sidebar.TFrame", background=COLORS["sidebar"])
    style.configure("NavIndicator.TFrame", background=COLORS["primary"])
    style.configure("NavIndicatorMuted.TFrame", background=COLORS["sidebar"])
    style.configure(
        "Brand.TLabel",
        background=COLORS["sidebar"], foreground=COLORS["text"],
        font=("Microsoft YaHei UI", 16, "bold"),
    )
    style.configure(
        "BrandSub.TLabel",
        background=COLORS["sidebar"], foreground=COLORS["text_muted"],
        font=("Bahnschrift", 7),
    )
    style.configure(
        "NavSection.TLabel",
        background=COLORS["sidebar"], foreground=COLORS["text_muted"],
        font=("Microsoft YaHei UI", 8, "bold"),
    )
    style.configure(
        "Nav.TButton",
        background=COLORS["sidebar"], foreground=COLORS["sidebar_text"],
        borderwidth=0, anchor="w", padding=(12, 4), font=FONT_BODY,
    )
    style.map(
        "Nav.TButton",
        background=[("active", COLORS["sidebar_hover"])],
        foreground=[("active", COLORS["text"])],
    )
    style.configure(
        "NavActive.TButton",
        background=COLORS["sidebar_active"], foreground=COLORS["text"],
        borderwidth=0, anchor="w", padding=(12, 4), font=FONT_BODY_MEDIUM,
    )
    style.map(
        "NavActive.TButton",
        background=[("active", COLORS["sidebar_hover"])],
        foreground=[("active", COLORS["text"])],
    )
    style.configure(
        "SidebarStatus.TLabel",
        background=COLORS["sidebar"], foreground=COLORS["text_muted"],
        font=("Microsoft YaHei UI", 8),
    )

    style.configure(
        "PageTitle.TLabel",
        background=COLORS["background"], foreground=COLORS["text"], font=FONT_TITLE,
    )
    style.configure(
        "PageSub.TLabel",
        background=COLORS["background"], foreground=COLORS["text_muted"], font=FONT_BODY,
    )
    style.configure(
        "StatusChip.TLabel",
        background=COLORS["background"], foreground=COLORS["accent"],
        font=("Microsoft YaHei UI", 8, "bold"), padding=(0, 5),
    )
    style.configure(
        "MutedStatus.TLabel",
        background=COLORS["background"], foreground=COLORS["text_muted"],
        font=("Microsoft YaHei UI", 8), padding=(0, 5),
    )

    style.configure(
        "Card.TFrame",
        background=COLORS["surface"], relief="solid", borderwidth=1,
        bordercolor=COLORS["border"],
    )
    style.configure("CardTitle.TLabel", background=COLORS["surface"], foreground=COLORS["text"], font=FONT_SECTION)
    style.configure("CardText.TLabel", background=COLORS["surface"], foreground=COLORS["text_muted"], font=FONT_BODY)
    style.configure("KpiValue.TLabel", background=COLORS["surface"], foreground=COLORS["text"], font=FONT_METRIC)
    style.configure("KpiLabel.TLabel", background=COLORS["surface"], foreground=COLORS["text_muted"], font=FONT_BODY)
    style.configure("KpiHint.TLabel", background=COLORS["surface"], foreground=COLORS["primary"], font=("Microsoft YaHei UI", 8, "bold"))
    style.configure("KpiHintMuted.TLabel", background=COLORS["surface"], foreground=COLORS["text_muted"], font=("Microsoft YaHei UI", 8))
    for icon_style in ("KpiIconBlue.TLabel", "KpiIconGreen.TLabel", "KpiIconOrange.TLabel", "KpiIconRed.TLabel"):
        style.configure(
            icon_style, background=COLORS["surface"], foreground=COLORS["primary"],
            font=("Microsoft YaHei UI", 8, "bold"), padding=(0, 0),
        )
    style.configure("DriverValue.TLabel", background=COLORS["surface"], foreground=COLORS["text"], font=("Segoe UI", 9, "bold"))
    style.configure("Toolbar.TFrame", background=COLORS["surface"], relief="solid", borderwidth=1, bordercolor=COLORS["border"])
    style.configure("Toolbar.TLabel", background=COLORS["surface"], foreground=COLORS["text_muted"], font=FONT_BODY)

    # Status stays textual: no filled badge blocks.
    tag_colors = {
        "TagBlue.TLabel": COLORS["primary"],
        "TagGreen.TLabel": COLORS["accent"],
        "TagOrange.TLabel": COLORS["warning"],
        "TagRed.TLabel": COLORS["danger"],
        "TagGray.TLabel": COLORS["text_muted"],
    }
    for tag_style, foreground in tag_colors.items():
        style.configure(
            tag_style, background=COLORS["surface"], foreground=foreground,
            font=("Microsoft YaHei UI", 8, "bold"), padding=(0, 4),
        )

    # Rank / list row styles
    style.configure("RankTop.TLabel", background=COLORS["surface"], foreground=COLORS["primary"], font=("Segoe UI", 11, "bold"))
    style.configure("RankNormal.TLabel", background=COLORS["surface"], foreground=COLORS["text_muted"], font=("Segoe UI", 11, "bold"))
    style.configure("RankName.TLabel", background=COLORS["surface"], foreground=COLORS["text"], font=FONT_BODY_MEDIUM)
    style.configure("RankMeta.TLabel", background=COLORS["surface"], foreground=COLORS["text_muted"], font=("Microsoft YaHei UI", 8))
    style.configure("RankValue.TLabel", background=COLORS["surface"], foreground=COLORS["text"], font=("Segoe UI", 13, "bold"))

    # Summary row styles
    style.configure("SummaryLabel.TLabel", background=COLORS["surface"], foreground=COLORS["text_muted"], font=FONT_BODY)
    style.configure("SummaryValue.TLabel", background=COLORS["surface"], foreground=COLORS["text"], font=FONT_BODY_MEDIUM)
    style.configure(
        "FormError.TLabel",
        background=COLORS["surface"],
        foreground=COLORS["danger"],
        font=FONT_BODY,
    )
    style.configure(
        "LedgerSpine.TFrame",
        background=COLORS["surface"],
        relief="solid",
        borderwidth=1,
        bordercolor=COLORS["border"],
    )
    style.configure(
        "SpineLabel.TLabel",
        background=COLORS["surface"], foreground=COLORS["text_muted"],
        font=("Microsoft YaHei UI", 8),
    )
    style.configure(
        "SpineValue.TLabel",
        background=COLORS["surface"], foreground=COLORS["text"],
        font=("Bahnschrift", 10, "bold"),
    )
    style.configure(
        "SpineArrow.TLabel",
        background=COLORS["surface"], foreground=COLORS["primary"],
        font=("Bahnschrift", 10),
    )
    style.configure(
        "Finance.Horizontal.TProgressbar",
        background=COLORS["primary"],
        troughcolor=COLORS["surface_muted"],
        bordercolor=COLORS["surface_muted"],
        lightcolor=COLORS["primary"],
        darkcolor=COLORS["primary"],
        thickness=6,
    )
    style.configure(
        "FinanceReceipt.Horizontal.TProgressbar",
        background=COLORS["accent"],
        troughcolor=COLORS["surface_muted"],
        bordercolor=COLORS["surface_muted"],
        lightcolor=COLORS["accent"],
        darkcolor=COLORS["accent"],
        thickness=6,
    )

    # AI operating workspace: white rows separated by structure, not chat bubbles.
    style.configure("ChatPane.TFrame", background=COLORS["surface"])
    style.configure("ChatThread.TFrame", background=COLORS["surface"])
    style.configure(
        "ChatAssistant.TFrame",
        background=COLORS["surface"],
        relief="flat",
        borderwidth=0,
    )
    style.configure(
        "ChatUser.TFrame",
        background=COLORS["surface"],
        relief="flat",
        borderwidth=0,
    )
    style.configure(
        "ChatCandidate.TFrame",
        background=COLORS["surface"],
        relief="solid",
        borderwidth=1,
        bordercolor=COLORS["border"],
    )
    style.configure(
        "ChatAssistant.TLabel",
        background=COLORS["surface"],
        foreground=COLORS["text"],
        font=FONT_BODY,
    )
    style.configure(
        "ChatUser.TLabel",
        background=COLORS["surface"],
        foreground=COLORS["text"],
        font=FONT_BODY,
    )
    style.configure(
        "ChatCandidateTitle.TLabel",
        background=COLORS["surface"],
        foreground=COLORS["text"],
        font=FONT_BODY_MEDIUM,
    )
    style.configure(
        "ChatCandidateText.TLabel",
        background=COLORS["surface"],
        foreground=COLORS["text_muted"],
        font=FONT_BODY,
    )
    style.configure(
        "ChatMeta.TLabel",
        background=COLORS["surface"],
        foreground=COLORS["text_muted"],
        font=("Microsoft YaHei UI", 8),
    )
    style.configure(
        "ContextValue.TLabel",
        background=COLORS["surface"],
        foreground=COLORS["text"],
        font=FONT_BODY_MEDIUM,
    )

    for tree_style in (
        "Treeview", "primary.Treeview", "info.Treeview", "success.Treeview",
        "warning.Treeview", "danger.Treeview", "secondary.Treeview",
    ):
        style.configure(
            tree_style,
            background=COLORS["surface"], fieldbackground=COLORS["surface"],
            foreground=COLORS["text"], rowheight=SPACING["row_height"], font=FONT_BODY,
            bordercolor=COLORS["border"], lightcolor=COLORS["border"], darkcolor=COLORS["border"],
        )
        style.map(
            tree_style,
            background=[("selected", COLORS["primary_soft"])],
            foreground=[("selected", COLORS["text"])],
        )
    for heading_style in (
        "Treeview.Heading", "primary.Treeview.Heading", "info.Treeview.Heading",
        "success.Treeview.Heading", "warning.Treeview.Heading",
        "danger.Treeview.Heading", "secondary.Treeview.Heading",
    ):
        style.configure(
            heading_style,
            background=COLORS["surface"], foreground=COLORS["text_muted"],
            font=FONT_BODY_MEDIUM, padding=SPACING["table_head_pad"], relief="flat",
        )

    for notebook_style in ("TNotebook", "primary.TNotebook"):
        style.configure(notebook_style, background=COLORS["background"], borderwidth=0)
    for tab_style in ("TNotebook.Tab", "primary.TNotebook.Tab"):
        style.configure(
            tab_style,
            background=COLORS["surface"],
            foreground=COLORS["text_muted"],
            font=FONT_BODY,
            padding=(13, 8),
            borderwidth=0,
        )
        style.map(
            tab_style,
            background=[("selected", COLORS["surface"])],
            foreground=[("selected", COLORS["primary"])],
        )

    # All actions use white or transparent surfaces; hierarchy comes from borders and text.
    button_palettes = {
        "primary.TButton": (COLORS["surface"], COLORS["text"], COLORS["primary"], COLORS["primary_soft"]),
        "success.TButton": (COLORS["surface"], COLORS["accent"], COLORS["border"], COLORS["surface_muted"]),
        "info.TButton": (COLORS["surface"], COLORS["primary"], COLORS["border"], COLORS["surface_muted"]),
        "warning.TButton": (COLORS["surface"], COLORS["warning"], COLORS["border"], COLORS["surface_muted"]),
        "danger.TButton": (COLORS["surface"], COLORS["danger"], COLORS["border"], COLORS["surface_muted"]),
        "secondary.TButton": (COLORS["surface"], COLORS["text_muted"], COLORS["border"], COLORS["surface_muted"]),
    }
    for button_style, (background, foreground, border, active) in button_palettes.items():
        style.configure(
            button_style,
            font=FONT_CONTROL,
            padding=SPACING["button_pad"],
            background=background,
            foreground=foreground,
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
            focuscolor=COLORS["primary"],
        )
        style.map(
            button_style,
            background=[("pressed", active), ("active", active)],
            foreground=[("disabled", "#94A3B8")],
        )

    outline_palettes = {
        "primary.Outline.TButton": (COLORS["text"], COLORS["primary"], COLORS["primary_soft"]),
        "success.Outline.TButton": (COLORS["accent"], COLORS["border"], COLORS["surface_muted"]),
        "info.Outline.TButton": (COLORS["primary"], COLORS["border"], COLORS["surface_muted"]),
        "warning.Outline.TButton": (COLORS["warning"], COLORS["border"], COLORS["surface_muted"]),
        "danger.Outline.TButton": (COLORS["danger"], COLORS["border"], COLORS["surface_muted"]),
        "secondary.Outline.TButton": (COLORS["text_muted"], COLORS["border"], COLORS["surface_muted"]),
    }
    for button_style, (foreground, border, active) in outline_palettes.items():
        style.configure(
            button_style,
            font=FONT_CONTROL,
            padding=SPACING["button_pad"],
            background=COLORS["surface"],
            foreground=foreground,
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
            focuscolor=COLORS["primary"],
        )
        style.map(button_style, background=[("pressed", active), ("active", active)])

    style.configure(
        "Outline.TButton",
        font=FONT_CONTROL,
        padding=SPACING["button_pad"],
        background=COLORS["surface"],
        foreground=COLORS["text_muted"],
        bordercolor=COLORS["border"],
        lightcolor=COLORS["border"],
        darkcolor=COLORS["border"],
        focuscolor=COLORS["primary"],
    )
    style.map(
        "Outline.TButton",
        background=[("pressed", COLORS["surface_muted"]), ("active", COLORS["surface_muted"])],
    )

    for link_style in ("Link.TButton", "primary.Link.TButton"):
        style.configure(
            link_style, background=COLORS["surface"], foreground=COLORS["primary"],
            borderwidth=0, padding=(4, 4), font=FONT_BODY,
        )
        style.map(link_style, foreground=[("active", COLORS["primary_hover"])])


def style_dialog(
    dialog,
    parent,
    width,
    height,
    *,
    resizable=True,
    min_width=None,
    min_height=None,
):
    """Apply screen-safe modal behavior and center it over the application."""
    owner = parent.winfo_toplevel()
    dialog.transient(owner)
    dialog.grab_set()
    dialog.resizable(resizable, resizable)
    dialog.update_idletasks()

    work_left, work_top, work_right, work_bottom = working_area(dialog)
    work_width = work_right - work_left
    work_height = work_bottom - work_top
    frame_width, frame_height = window_frame_size(dialog)
    scaled_width = scale_px(dialog, width)
    scaled_height = scale_px(dialog, height)
    safe_width = min(
        scaled_width,
        max(scale_px(dialog, 420), work_width - 80 - frame_width),
    )
    safe_height = min(
        scaled_height,
        max(scale_px(dialog, 320), work_height - 80 - frame_height),
    )
    if resizable:
        dialog.minsize(
            min(
                scale_px(dialog, min_width)
                if min_width
                else min(scale_px(dialog, 480), safe_width),
                safe_width,
            ),
            min(
                scale_px(dialog, min_height)
                if min_height
                else min(scale_px(dialog, 320), safe_height),
                safe_height,
            ),
        )

    owner_frame_width, owner_frame_height = window_frame_size(owner)
    owner_outer_width = owner.winfo_width() + owner_frame_width
    owner_outer_height = owner.winfo_height() + owner_frame_height
    x = owner.winfo_x() + max(
        0, (owner_outer_width - safe_width - frame_width) // 2
    )
    y = owner.winfo_y() + max(
        0, (owner_outer_height - safe_height - frame_height) // 2
    )
    max_x = work_right - safe_width - frame_width - 20
    max_y = work_bottom - safe_height - frame_height - 20
    x = min(max(work_left + 20, x), max(work_left + 20, max_x))
    y = min(max(work_top + 20, y), max(work_top + 20, max_y))
    dialog.geometry(f"{safe_width}x{safe_height}+{x}+{y}")
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    dialog.after_idle(lambda: scale_treeview_columns(dialog))
