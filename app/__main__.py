import uvicorn
from app.main import create_app
from app.config import load_config


def main():
    cfg = load_config("config.yaml")
    app = create_app(cfg, session_secret=cfg.session_secret)
    uvicorn.run(app, host=cfg.host, port=cfg.port)


if __name__ == "__main__":
    main()