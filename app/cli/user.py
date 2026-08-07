import click
from pathlib import Path
from app.storage.shared_db import init_shared_db, create_user, get_user_by_username
from app.auth.passwords import hash_password

_db_dir: Path | None = None


def _resolve_db_dir() -> Path:
    global _db_dir
    if _db_dir is not None:
        return _db_dir
    from app.config import load_config
    cfg = load_config("config.yaml")
    _db_dir = cfg.db_dir
    return _db_dir


@click.group()
def cli() -> None:
    pass


@cli.command("create")
@click.option("--username", required=True)
@click.option("--password", default=None)
@click.option("--admin", is_flag=True, default=False)
def create_cmd(username: str, password: str | None, admin: bool) -> None:
    if not password:
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
    conn = init_shared_db(_resolve_db_dir())
    try:
        create_user(conn, username, hash_password(password), is_admin=admin)
        click.echo(f"Created user '{username}'{' (admin)' if admin else ''}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise click.exceptions.Exit(1)
    finally:
        conn.close()


@cli.command("passwd")
@click.option("--username", required=True)
@click.option("--password", default=None)
def passwd_cmd(username: str, password: str | None) -> None:
    if not password:
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
    conn = init_shared_db(_resolve_db_dir())
    try:
        user = get_user_by_username(conn, username)
        if not user:
            click.echo(f"Error: user '{username}' not found", err=True)
            raise click.exceptions.Exit(1)
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (hash_password(password), username),
        )
        conn.commit()
        click.echo(f"Password updated for '{username}'")
    finally:
        conn.close()


if __name__ == "__main__":
    cli()