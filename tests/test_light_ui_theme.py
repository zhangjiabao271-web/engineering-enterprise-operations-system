import unittest

from ui.theme import COLORS, FONT_TITLE, configure_design_system


def _relative_luminance(hex_color):
    channels = [
        int(hex_color[index:index + 2], 16) / 255
        for index in (1, 3, 5)
    ]
    linear = [
        channel / 12.92
        if channel <= 0.04045
        else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(first, second):
    values = sorted((_relative_luminance(first), _relative_luminance(second)))
    return (values[1] + 0.05) / (values[0] + 0.05)


class _FakeStyle:
    def __init__(self):
        self.configurations = {}
        self.maps = {}

    def configure(self, name, **options):
        self.configurations[name] = options

    def map(self, name, **options):
        self.maps[name] = options


class _FakeRoot:
    def __init__(self):
        self.style = _FakeStyle()
        self.options = {}

    def configure(self, **options):
        self.options.update(options)


class LightUiThemeTests(unittest.TestCase):
    def setUp(self):
        self.root = _FakeRoot()
        configure_design_system(self.root)

    def test_palette_is_fixed_light_and_text_is_accessible(self):
        self.assertEqual(COLORS["background"], "#FFFFFF")
        self.assertEqual(COLORS["surface"], "#FFFFFF")
        self.assertEqual(COLORS["sidebar"], "#FFFFFF")
        for key in ("text", "text_muted", "primary", "accent"):
            self.assertGreaterEqual(
                _contrast(COLORS[key], COLORS["surface"]),
                4.5,
                key,
            )

    def test_navigation_tags_kpis_and_chat_have_no_colored_fill(self):
        styles = self.root.style.configurations
        white_styles = (
            "Sidebar.TFrame",
            "Nav.TButton",
            "NavActive.TButton",
            "Card.TFrame",
            "KpiIconBlue.TLabel",
            "KpiIconGreen.TLabel",
            "KpiIconOrange.TLabel",
            "KpiIconRed.TLabel",
            "TagBlue.TLabel",
            "TagGreen.TLabel",
            "TagOrange.TLabel",
            "TagRed.TLabel",
            "ChatAssistant.TFrame",
            "ChatUser.TFrame",
        )
        for style_name in white_styles:
            self.assertEqual(
                styles[style_name]["background"],
                COLORS["surface"],
                style_name,
            )

    def test_action_buttons_are_white_with_visible_borders(self):
        styles = self.root.style.configurations
        for style_name in (
            "primary.TButton",
            "success.TButton",
            "info.TButton",
            "warning.TButton",
            "danger.TButton",
            "secondary.TButton",
        ):
            self.assertEqual(styles[style_name]["background"], COLORS["surface"])
            self.assertTrue(styles[style_name]["bordercolor"])

    def test_typography_and_readonly_inputs_match_precision_ui(self):
        self.assertEqual(FONT_TITLE, ("Microsoft YaHei UI", 18))
        combobox_map = self.root.style.maps["TCombobox"]
        self.assertIn(
            ("readonly", COLORS["surface"]),
            combobox_map["fieldbackground"],
        )
        self.assertEqual(
            self.root.style.configurations["FormError.TLabel"]["background"],
            COLORS["surface"],
        )


if __name__ == "__main__":
    unittest.main()
