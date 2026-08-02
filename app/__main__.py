import uvicorn
from app.main import create_app
from app.config import load_config


def main():
    cfg = load_config("config.yaml")
    app = create_app(cfg, session_secret="change-me-in-production")
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()