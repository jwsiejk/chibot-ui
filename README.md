# Ask Chip — Full Build (Voice + Visemes + Modular Services)

Clean, working Ask Chip with modular `services/` and `server/`, conversational voice, and canvas visemes.

## Local run
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export FLASK_ENV=development
python app.py
# open http://localhost:5000
```

## Render
Build: `pip install -r requirements.txt`  
Start: `gunicorn app:app`  
Env: SECRET_KEY, DATABASE_URL (sslmode=require), OPENAI_API_KEY (opt), OPENAI_MODEL (opt), ELEVEN_API_KEY/VOICE_ID (opt), ELEVEN_MODEL_ID (opt)

Notes: If ElevenLabs is not configured, browser speechSynthesis is used and visemes run with heuristics.
