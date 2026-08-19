"""Pure helpers for searchable desktop selection controls."""


def _search_text(value):
    return " ".join(str(value or "").strip().casefold().split())


def filter_supplier_offer_labels(offers_by_label, query, limit=50):
    """Return ranked offer labels matching a material name or specification."""
    query_text = _search_text(query)
    if not query_text:
        return list(offers_by_label)[:limit]

    matches = []
    for position, (label, offer) in enumerate(offers_by_label.items()):
        name = _search_text(offer.get("name"))
        specification = _search_text(offer.get("specification"))
        unit = _search_text(offer.get("unit"))
        label_text = _search_text(label)

        if name == query_text:
            rank = 0
        elif name.startswith(query_text):
            rank = 1
        elif specification.startswith(query_text):
            rank = 2
        elif query_text in name:
            rank = 3
        elif query_text in specification:
            rank = 4
        elif query_text in unit or query_text in label_text:
            rank = 5
        else:
            continue
        matches.append((rank, position, label))

    matches.sort(key=lambda item: (item[0], item[1]))
    return [label for _rank, _position, label in matches[:limit]]
