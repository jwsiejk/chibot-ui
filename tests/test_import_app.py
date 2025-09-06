
def test_import_app():
    import importlib.util, sys, os
    sys.path.insert(0, os.getcwd())
    spec = importlib.util.spec_from_file_location("app_pkg", os.path.join("app","__init__.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
