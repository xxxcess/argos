from html.parser import HTMLParser
from pathlib import Path


class _ScriptParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag != "script":
            return
        self.scripts.append(dict(attrs))


def _script_srcs():
    parser = _ScriptParser()
    parser.feed((Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8"))
    return [attrs.get("src") for attrs in parser.scripts if attrs.get("src")]


def test_index_does_not_eager_load_app_import_graph_twice():
    srcs = _script_srcs()

    assert "/static/app.js" in srcs
    assert "/static/js/chat.js?v=20260609ws" not in srcs

    app_imported_modules = {
        "/static/js/storage.js",
        "/static/js/ui.js",
        "/static/js/markdown.js",
        "/static/js/sessions.js",
        "/static/js/chat.js",
        "/static/js/document.js",
        "/static/js/gallery.js",
        "/static/js/settings.js",
        "/static/js/admin.js",
    }
    assert app_imported_modules.isdisjoint(srcs)
