"""WSGI entry point: ``waitress-serve --call wsgi:app`` or ``gunicorn wsgi:app``."""

from mediaserver import create_app

app = create_app()
