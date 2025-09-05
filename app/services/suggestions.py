from ..db import db
def trim_words(label: str, max_words: int) -> str:
    return " ".join(label.split()[:max_words])
def hygienic_suggestions(reply: str | None = None):
    cfg = db.get_config()
    max_items = int(cfg.get("suggestions_max_items", 4))
    max_words = int(cfg.get("suggestions_max_words", 7))
    base = [
        {"id":"steps","label":"Show install steps"},
        {"id":"value","label":"Explain the business value"},
        {"id":"compare","label":"Compare options briefly"},
        {"id":"next","label":"Summarize next actions"},
    ]
    out=[]; 
    for item in base[:max_items]:
        out.append({"id": item["id"], "label": trim_words(item["label"], max_words)})
    return out[:max_items]
