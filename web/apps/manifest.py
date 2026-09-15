"""Canonical app list backing the GET / app index in web/server.py.

Each record maps an app to its served roots and entry blueprint module. The
app index consumes name/description/route to render links; served_roots and
entry_module are metadata for tooling.

No Flask import, no git, no I/O: safe to import anywhere without side effects.
"""

APPS: list[dict] = [
    {
        "name": "Music",
        "description": "Score-text music workbench: parse, audition, play, and FLAC-render via offline FluidSynth (WYHIWYG).",
        "entry_module": "web.apps.music",
        "served_roots": ["web/music.html", "web/static", "appdata/webmusicdata"],
        "route": "/music",
    },
]


def all_apps() -> list[dict]:
    """Return all app manifest records."""
    return APPS


def app_by_route(route: str) -> "dict | None":
    """Return the manifest record whose route matches, or None."""
    return next((a for a in APPS if a["route"] == route), None)


def app_record(name: str) -> "dict | None":
    """Return the manifest record with the given name, or None."""
    return next((a for a in APPS if a["name"] == name), None)


def route_for_path(path: str) -> "str | None":
    """Return the route of the app whose route is the longest prefix of `path`.

    A route matches when `path == route` or `path` starts with `route + "/"`.
    Returns None if no app route matches (e.g. "/", since no app route is "/").
    """
    matches = [
        a["route"] for a in APPS
        if path == a["route"] or path.startswith(a["route"] + "/")
    ]
    if not matches:
        return None
    return max(matches, key=len)
