
# app/services/persona_prompt.py
from typing import Dict, Any, List

def build_persona_preamble(persona: Dict[str, Any]) -> str:
    n = persona.get('id','Chip')
    ne = persona.get('nebraska_persona_level', 0.13)
    return (f"You are {n}, a Nebraska ex-farmer turned tech genius VSE. "
            f"Keep personality frequency ~{ne*100:.0f}% (deadpan). Be brief and conversational.")

def format_kb_context(snippets: List[str]) -> str:
    if not snippets: return ""
    lines = "\\n".join(f"- {s.strip()}" for s in snippets)
    return f"Use these knowledge snippets when helpful:\\n{lines}\\n"
