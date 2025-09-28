from .rules import infer_intent, extract_entities
from .annotator import annotate
from .policy import decide

def infer(text: str, persona_id: str, meta: dict, store):
    base = infer_intent(text, persona_id, store)
    ents = extract_entities(base["intent"], text)
    tags = annotate(meta or {})
    base.update({"entities": ents, "tags": tags})
    return base
