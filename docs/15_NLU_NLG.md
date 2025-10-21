### `docs/15_NLU_NLG.md`

```markdown
# NLU & NLG Contracts

## NLU (after ASR final)
Input: final ASR text (+ optional partials for hints)  
Output (exactly one object per turn):
```json
{
  "intent": "troubleshoot_install",
  "entities": {"product":"FlashArray","version":"4.3"},
  "confidence": 0.86,
  "tone": "uncertain",
  "domain": "installation",
  "safety": {"pii": false, "off_policy": false},
  "hints": ["suggest_steps"]
}
NLG (before TTS)
Input: { nlu, dialog_context, persona }
Output (exactly one object per turn):

json
Copy code
{
  "text": "Let’s run through a quick install check…",
  "style": "step_by_step",
  "speak_as": "chip",
  "annotations": {"pace":"medium"},
  "next_actions": ["offer_steps"],
  "safety_notes": null
}
Integration points
Engine calls NLU after asr.final, logs NLU, then passes NLU → Dialog Policy → NLG, logs NLG, then schedules TTS.

Policy may clamp next-turn auto_commit_when_ready based on NLU confidence/safety.

Exporter writes one NLU and one NLG object per turn into the bundle.