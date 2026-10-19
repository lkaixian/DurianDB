# 09 Dashboards and the extension question

Written 2026-10-20. Sources: the vendor's public UI documentation (overview pages only, the deeper pages were not retrievable), the lab instance's module list, and the contents of its general-purpose JSON store, read through the REST API with an admin account (black-box). No vendor code was read.

## What the studied product does

- UI configuration is stored as ordinary JSON documents in a general store, not in dedicated tables. A fresh install holds three: pages (`mangoUI-pages`), menu (`mangoUI-menu`) and settings (`mangoUI-settings`). Each carries a name, an id, a revision, `readPermission` and `editPermission` role lists (default read: user; the settings document is also readable by anonymous). Pages and menu are empty on a clean install.
- A "page" is HTML written with the product's component library (`<ma-*>` elements on AngularJS and Angular Material), so a page is code that runs in the viewer's session.
- The dashboard designer is a drag-and-drop canvas that generates that markup. Its output can be edited as code afterwards. Widgets bind to watch lists, and a dynamic workflow lets viewers pick points or lists that the page then follows (parameters carried in the URL state).
- Menu items have an icon, a label, a URL, permissions and a flag for the date bar. They can be hidden, reordered and added.
- Pages can restyle themselves from live values (opacity, colour, blur, animation speed bound to a point).
- Custom modules, themes and CSS are possible. Permissions, sharing and public access are described only by name in the pages that could be read.

## Lessons

1. UI config as a generic JSON document with role lists is a good idea: no schema changes for a new page, and permissions come for free. DurianDB already keeps config that way.
2. Pages that are code are powerful and dangerous. Anyone who can save a page can run script as every viewer, so edit permission is effectively administrator permission. That is the stored-XSS class of problem by design.
3. Visibility of a page and access to the data it shows are separate questions and need to stay separate.

## DurianDB's design (`durian/dashboards.py`, `web/dashboards.js`)

A dashboard is data: title, columns, refresh period, read roles, edit roles, owner, and a list of widgets. Each widget has an id, a type, a title, a place on the grid (`x y w h`) and type-specific settings. Nothing an author saves is parsed as HTML or executed.

| Studied | DurianDB | Why |
|---|---|---|
| Page as HTML and script | Widgets as JSON, rendered by code that ships with the server | A saved dashboard can never run code, so the strict content policy (no inline script or style) stays on. |
| Restyle from values with free CSS bindings | Rules `{op, value, tone}` with three fixed tones (normal, warn, alarm) | Same idea, bounded. Tones also use weight and underline, not colour alone. |
| Menu items | Dashboards list, plus `#d/name` links | One mechanism instead of two. |
| Edit permission | Owner, `edit_roles`, or superadmin | Creating needs the `designer` role. Only a superadmin can make one public. |
| Public pages | A role `anonymous` in the read roles. Everyone, signed in or not, holds it | "Public" means everyone. Fixed along the way: a user with only a custom role did not count as anonymous. |
| (unclear) | Public dashboard does not make its data public. Saving warns when a public dashboard shows a point anonymous cannot read | The common mistake is publishing a page and assuming its data is published. The warning names each point. |
| Designer canvas | Form with move and size buttons, a settings box per widget, a live preview | Keyboard usable and testable; drag and drop is a later layer over the same data. |

Widget types now: `value` (with rules), `gauge` (zones), `chart` (up to 6 series, distinguished by line pattern and label), `alarms`, `table` (by tag, watch list or names), `setpoint`, `text` (plain text only), `status` (source health).

Validation reports every problem with a path (`widgets[2].point`), checks the grid (fit, overlap), and returns warnings separately from errors.

### The extension story

Adding a widget type is two small, reviewable pieces: a validator registered with `@widget("name")` in `durian/dashboards.py` and a `registerWidget("name", {...})` renderer in `web/dashboards.js`. Whoever runs the server installs them. There is deliberately no way to upload widget code through the UI: with a strict content policy, uploaded code could not run, and loosening the policy to allow it would give every designer the power the studied product's pages give. If untrusted authors ever need custom visuals, the safe route is a sandboxed frame with its own origin, which is not built.

## Not built

Drag and drop, templates and import/export of a dashboard on its own (the JSON is already portable), URL parameters that retarget a dashboard, per-user dashboards, themes, and a menu editor.

## Checked

`tests/test_dashboards.py`: every widget type validates, errors carry paths, overlap and grid fit, warnings, and a permission matrix covering anonymous, viewer, designer, other designer, a custom role, edit roles, owner immutability, hidden names not revealed, public publishing, and audit entries. `tools/browser_dashboard.mjs` in a real browser: the seeded dashboard with chart, gauge, alarms, table, status and a working setpoint; the designer including a rejected save; script text staying inert; a logged-out visitor seeing a public dashboard but not its data; kiosk mode; phone width; dark mode.
