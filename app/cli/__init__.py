import typer

from app.cli.init import init_command
from app.cli.seed import seed_command
from app.cli.users import create_user_command

cli = typer.Typer(
    name="desk-curfew",
    help="CLI для desk-curfew-server",
    no_args_is_help=True,
)

# Регистрируем команды через cli.command()(func)
cli.command(name="create-user")(create_user_command)
cli.command(name="seed")(seed_command)
cli.command(name="init")(init_command)
