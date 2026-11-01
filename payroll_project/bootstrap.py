"""Load CLI-provisioned runtime values without overriding injected configuration."""
import os
from pathlib import Path


def load_runtime_environment():
    if os.environ.get('DJANGO_LOAD_DOTENV', 'True').strip().lower() in {'1', 'true', 'yes', 'on'}:
        from dotenv import load_dotenv
        root = Path(__file__).resolve().parent.parent
        load_dotenv(root / '.env.local', override=False)
        load_dotenv(root / '.env', override=False)


def select_migration_database():
    load_runtime_environment()
    if os.environ.get('DATABASE_URL_UNPOOLED'):
        os.environ['DATABASE_URL'] = os.environ['DATABASE_URL_UNPOOLED']
        os.environ['DB_POOLED'] = 'False'
