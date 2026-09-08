"""BD temporal en disco: LiveServer no debe compartir una conexión SQLite
en memoria entre solicitudes concurrentes. Nunca apunta a db.sqlite3.
"""
from copy import deepcopy
from pathlib import Path
import tempfile
import uuid

from config.settings import *  # noqa: F403

DATABASES = deepcopy(DATABASES)  # noqa: F405
if DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3':
    DATABASES['default']['TEST'] = {
        'NAME': str(Path(tempfile.gettempdir()) / f'captura-test-{uuid.uuid4().hex}.sqlite3'),
    }
