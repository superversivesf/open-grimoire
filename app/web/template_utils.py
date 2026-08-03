import markdown as md_lib
from fastapi.templating import Jinja2Templates
from pathlib import Path


def create_templates(directory: str) -> Jinja2Templates:
    templates = Jinja2Templates(directory=directory)
    templates.env.filters["md"] = lambda text: md_lib.markdown(text, extensions=["nl2br"]) if text else ""
    return templates