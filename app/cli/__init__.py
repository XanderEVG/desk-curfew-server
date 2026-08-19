import typer

from app.cli.init import init_command
from app.cli.seed import seed_command
from app.cli.users import create_user_command

cli = typer.Typer(
    name="desk-curfew",
    help="CLI для desk-curfew-server",
    no_args_is_help=True,
)

# Пользователи
cli.add_command(create_user_command, name="create-user")

# Фикстуры
cli.add_command(seed_command, name="seed")

# Инициализация
cli.add_command(init_command, name="init")