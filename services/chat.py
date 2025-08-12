# services/chat.py
# Placeholder: wire your OpenAI/model call here.
# Keep the signature identical to the route deps.

def generate_chip_response(user_id: str, name: str, text: str, role: str, region: str) -> str:
    # TODO: call your LLM with system prompt & guardrails
    # For now, echo with minimal formatting so routes run:
    return f"{text}"
