"""Make attendance_audit tamper-resistant IN THE DATABASE.

Rows can be inserted. They can never be deleted, and their content can never be updated. The only
UPDATEs allowed are the ones Django performs by itself when a linked record is deleted: setting the
`attendance_id`, `changed_by_id` or `correction_request_id` link to NULL. Even raw SQL from the
application's own database account is refused.

A future retention job must drop these triggers deliberately (see `reverse` below) rather than
deleting rows quietly.
"""
from django.db import migrations

PG_FORWARD = """
CREATE FUNCTION attendance_audit_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'attendance_audit rows cannot be deleted';
    END IF;
    IF (NEW.id, NEW.emp_id, NEW.date, NEW.record_id, NEW.action, NEW.old_values, NEW.new_values, NEW.reason, NEW.created_at)
       IS DISTINCT FROM
       (OLD.id, OLD.emp_id, OLD.date, OLD.record_id, OLD.action, OLD.old_values, OLD.new_values, OLD.reason, OLD.created_at) THEN
        RAISE EXCEPTION 'attendance_audit rows cannot be modified';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER attendance_audit_guard BEFORE UPDATE OR DELETE ON attendance_audit
    FOR EACH ROW EXECUTE FUNCTION attendance_audit_guard();
"""
PG_REVERSE = """
DROP TRIGGER IF EXISTS attendance_audit_guard ON attendance_audit;
DROP FUNCTION IF EXISTS attendance_audit_guard();
"""
SQLITE_FORWARD = [
    """CREATE TRIGGER attendance_audit_no_delete BEFORE DELETE ON attendance_audit
       BEGIN SELECT RAISE(ABORT, 'attendance_audit rows cannot be deleted'); END;""",
    """CREATE TRIGGER attendance_audit_no_update
       BEFORE UPDATE OF id, emp_id, date, record_id, action, old_values, new_values, reason, created_at
       ON attendance_audit
       BEGIN SELECT RAISE(ABORT, 'attendance_audit rows cannot be modified'); END;""",
]
SQLITE_REVERSE = ["DROP TRIGGER IF EXISTS attendance_audit_no_delete;", "DROP TRIGGER IF EXISTS attendance_audit_no_update;"]


def create_triggers(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == "postgresql":
        schema_editor.execute(PG_FORWARD)
    elif vendor == "sqlite":
        for sql in SQLITE_FORWARD:
            schema_editor.execute(sql)


def drop_triggers(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == "postgresql":
        schema_editor.execute(PG_REVERSE)
    elif vendor == "sqlite":
        for sql in SQLITE_REVERSE:
            schema_editor.execute(sql)


class Migration(migrations.Migration):
    dependencies = [("core", "0013_attendance_corrections")]
    operations = [migrations.RunPython(create_triggers, drop_triggers)]
