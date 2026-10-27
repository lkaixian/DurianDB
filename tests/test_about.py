"""About and version: /api/me carries the product identity, and the console shows it in the sidebar and on the About page. Run: python tests/test_about.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian import BUILD, REPO, VERSION  # noqa: E402
from durian.app import App  # noqa: E402
from _support import new_store  # noqa: E402

app = App(new_store())
app.auth.bootstrap("admin-password-123")
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))

assert VERSION.count(".") == 2 and VERSION[0].isdigit(), "the version is vMAJOR.MINOR.PATCH"  # the sidebar prints v + it
assert REPO.startswith("https://github.com/") and VERSION and BUILD

s, me = app.handle("GET", "/api/me", {}, None, admin)
assert s == 200 and (me["version"], me["build"], me["repo"]) == (VERSION, BUILD, REPO)

# the console reads all three from /api/me and nowhere else, so one change keeps the sidebar and the page in step
web = (Path(__file__).resolve().parent.parent / "web")
js, acct = (web / "app.js").read_text(), (web / "account.js").read_text()
assert 'id="nav"' in (web / "index.html").read_text() and "navver" in js and "viewAbout" in js and "about: viewAbout" in js
assert "me.version" in js and "me.build" in acct and "me.repo" in acct
assert ".navver" in (web / "app.css").read_text(), "the foot-of-sidebar bar needs its rule"
print("ok")
