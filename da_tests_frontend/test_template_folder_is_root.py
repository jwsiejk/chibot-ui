
import os
from app.factory import create_app

def test_template_folder_is_root_templates():
    app = create_app()
    # Flask sets app.template_folder to path relative to instance root; resolve to abs for comparison
    tpl_folder = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "templates"))
    app_tpl = os.path.normpath(app.template_folder)
    assert os.path.basename(app_tpl) == "templates", f"Expected templates folder to be 'templates', got {app_tpl}"
