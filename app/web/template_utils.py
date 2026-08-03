import markdown as md_lib
from fastapi.templating import Jinja2Templates
from pathlib import Path
from markupsafe import Markup


def create_templates(directory: str) -> Jinja2Templates:
    templates = Jinja2Templates(directory=directory)
    templates.env.filters["md"] = lambda text: Markup(md_lib.markdown(text, extensions=["nl2br"])) if text else Markup("")
    return templates