def annotate(text: str, meta: dict) -> dict:
    t = (text or '').lower()
    if 'how' in t or 'step' in t: move='offer_steps'
    elif 'compare' in t or 'vs' in t: move='compare_options'
    elif 'summarize' in t or 'next' in t: move='summarize_next_actions'
    else: move='none'
    return {'tone':'neutral','teacher_move': move}
