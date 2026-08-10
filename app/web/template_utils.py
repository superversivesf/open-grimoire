import markdown as md_lib
import nh3
from functools import lru_cache
from fastapi.templating import Jinja2Templates
from pathlib import Path
from markupsafe import Markup
from app.version import __version__

_MD_TAGS = nh3.ALLOWED_TAGS | {"img", "details", "summary", "del", "mark", "sup", "sub"}


@lru_cache(maxsize=256)
def _render_md_cached(text: str) -> Markup:
    if not text:
        return Markup("")
    html = md_lib.markdown(text, extensions=["nl2br"])
    return Markup(nh3.clean(html, tags=_MD_TAGS))


def _render_md(text: str | None) -> Markup:
    if not text:
        return Markup("")
    return _render_md_cached(text)


def create_templates(directory: str) -> Jinja2Templates:
    templates = Jinja2Templates(directory=directory)
    templates.env.filters["md"] = _render_md
    templates.env.globals["version"] = __version__
    templates.env.globals["is_admin"] = False  # overridden per-request in middleware
    templates.env.globals["csrf_token"] = None  # set per-request in middleware
    templates.env.globals["csp_nonce"] = None  # set per-request in middleware
    return templates