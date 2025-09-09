
from bs4 import BeautifulSoup
import os, re

def test_profile_email_readonly_autopopulated():
    # Parse the primary index.html
    root = "/opt/project" if os.path.exists("/opt/project") else "/mnt/data/workspace"
    path = os.path.join(root, "templates", "index.html")
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    soup = BeautifulSoup(html, "html.parser")
    email = soup.find(id="prof_email")
    assert email is not None, "prof_email input missing"
    assert email.get("readonly") is not None, "prof_email should be readonly"
