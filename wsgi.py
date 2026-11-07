"""WSGI entry point for production servers such as Gunicorn.

Note: importing this module intentionally does NOT initialize or mutate the
database (verified by tests). Before first serving a fresh deployment, run
the migration once (under the maintenance lock):

    python scripts/migrate_db.py

Local development can use ``python run.py`` which initializes the database.
"""
from app import app

application = app
