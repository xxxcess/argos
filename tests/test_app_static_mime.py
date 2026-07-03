import ast
import mimetypes
import os
from pathlib import Path


def _load_register_static_mime_types():
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    tree = ast.parse(app_path.read_text(encoding="utf-8"), filename=str(app_path))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "register_static_mime_types")
    module = ast.Module(body=[fn], type_ignores=[])
    ns = {"mimetypes": mimetypes}
    exec(compile(module, str(app_path), "exec"), ns)
    return ns["register_static_mime_types"]


def test_register_static_mime_types_restores_js_module_types():
    register_static_mime_types = _load_register_static_mime_types()
    original_js = mimetypes.types_map.get(".js")
    original_mjs = mimetypes.types_map.get(".mjs")
    try:
        mimetypes.types_map[".js"] = "text/plain"
        mimetypes.types_map.pop(".mjs", None)

        register_static_mime_types()

        assert mimetypes.types_map[".js"] == "text/javascript"
        assert mimetypes.types_map[".mjs"] == "application/javascript"
    finally:
        if original_js is None:
            mimetypes.types_map.pop(".js", None)
        else:
            mimetypes.types_map[".js"] = original_js

        if original_mjs is None:
            mimetypes.types_map.pop(".mjs", None)
        else:
            mimetypes.types_map[".mjs"] = original_mjs


def _load_read_html_template():
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    tree = ast.parse(app_path.read_text(encoding="utf-8"), filename=str(app_path))
    nodes = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_HTML_TEMPLATE_CACHE" for t in node.targets)
        )
        or (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_HTML_TEMPLATE_CACHE"
        )
        or (isinstance(node, ast.FunctionDef) and node.name == "_read_html_template")
    ]
    module = ast.Module(body=nodes, type_ignores=[])
    ns = {"os": os}
    exec(compile(module, str(app_path), "exec"), ns)
    return ns["_read_html_template"], ns["_HTML_TEMPLATE_CACHE"]


def test_read_html_template_caches_until_file_metadata_changes(tmp_path):
    read_html_template, cache = _load_read_html_template()
    page = tmp_path / "index.html"
    page.write_text("first", encoding="utf-8")

    assert read_html_template(str(page)) == "first"
    cached = cache[os.path.abspath(page)]
    page.write_text("second value", encoding="utf-8")

    assert read_html_template(str(page)) == "second value"
    assert cache[os.path.abspath(page)] != cached
