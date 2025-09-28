from app.services.mailer import send_email  # reuse your existing mail util

def send_flasharray_quickstart(to_email: str) -> None:
    subject = "FlashArray Quick Install – Step-by-Step"
    body_html = """
    <p>Here’s the short, safe path to bring up a FlashArray.</p>
    <ol>
      <li>Rack, power, mgmt cabling</li>
      <li>Setup Wizard: name, DNS/NTP, Support</li>
      <li>Hosts/volumes/mapping</li>
      <li>Validate I/O</li>
    </ol>
    <p>Reply if you want a live walkthrough.</p>
    """
    send_email(to=to_email, subject=subject, html=body_html)
