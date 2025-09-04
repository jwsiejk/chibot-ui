import secrets
CURRENT={"email":None,"csrf":None}
def issue_csrf(): t=secrets.token_hex(16); CURRENT["csrf"]=t; return t
def get_csrf(): return CURRENT.get("csrf")
def set_user(email): CURRENT["email"]=email
def get_user(): return CURRENT.get("email") or "user@example.com"
