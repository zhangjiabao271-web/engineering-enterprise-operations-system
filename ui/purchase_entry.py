"""State helpers for efficient, repeated purchase entry."""


CONTINUOUS_LINE_DEFAULTS = {
    "product": "",
    "material": "",
    "spec": "",
    "unit": "",
    "qty": "1",
    "material_unit_price": "",
    "tax_rate": "",
    "freight": "0.00",
    "tax_inclusive_unit_price": "--",
    "material_amount": "--",
    "tax_amount": "--",
    "project_cost": "--",
}


def reset_continuous_purchase_line(variables):
    """Clear one material line while preserving project and supplier context."""
    for key, value in CONTINUOUS_LINE_DEFAULTS.items():
        variables[key].set(value)
