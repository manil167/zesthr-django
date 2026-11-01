#!/usr/bin/env python
import os, sys

if __name__ == "__main__":
    # Locally: development (SQLite). On Vercel: production (Supabase Postgres).
    default_settings = (
        "payroll_project.settings.production"
        if os.environ.get("VERCEL")
        else "payroll_project.settings.development"
    )
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", default_settings)
    if len(sys.argv) > 1 and sys.argv[1] in {'migrate', 'prepare_deployment'}:
        from payroll_project.bootstrap import select_migration_database
        select_migration_database()
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)