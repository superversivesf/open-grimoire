import markdown as md_lib
from fastapi.templating import Jinja2Templates
from pathlib import Path
from markupsafe import Markup
from app.version import __version__


def create_templates(directory: str) -> Jinja2Templates:
    templates = Jinja2Templates(directory=directory)
    templates.env.filters["md"] = lambda text: Markup(md_lib.markdown(text, extensions=["nl2br"])) if text else Markup("")
    templates.env.globals["version"] = __version__
    templates.env.globals["is_admin"] = False  # overridden per-request in middleware
    return templates