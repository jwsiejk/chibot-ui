from ..db import db
def send_transcript(email, subject, body): db.add_email(email, subject, body); return True
