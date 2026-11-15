"""Rotating, private diagnostic logs that never store chat or credentials."""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import traceback
from pathlib import Path
from logging.handlers import RotatingFileHandler

from platformdirs import user_log_path

LOGGER = logging.getLogger('narrador')
MAX_LOG_BYTES = 2 * 1024 * 1024
LOG_BACKUPS = 3


def log_directory() -> Path:
    return Path(user_log_path('TikTokNarradorLive'))


def log_file() -> Path:
    return log_directory() / 'app.log'


def record_exception(context: str, error: BaseException) -> None:
    """Log exception type and source frames, excluding message text and local values."""
    frames = traceback.extract_tb(error.__traceback__)
    locations = ' <- '.join(
        f'{Path(frame.filename).name}:{frame.lineno} ({frame.name})' for frame in frames
    ) or 'sin marco disponible'
    LOGGER.error('%s; exception=%s; trace=%s', context, type(error).__name__, locations)


def configure_logging() -> Path:
    directory = log_directory()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        directory = Path(tempfile.gettempdir()) / 'TikTokNarradorLive' / 'logs'
        directory.mkdir(parents=True, exist_ok=True)
    if os.name == 'posix':
        try:
            directory.chmod(0o700)
        except OSError:
            pass
    if not any(isinstance(handler, RotatingFileHandler) for handler in LOGGER.handlers):
        handler = RotatingFileHandler(directory / 'app.log', maxBytes=MAX_LOG_BYTES,
                                      backupCount=LOG_BACKUPS, encoding='utf-8')
        handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s %(threadName)s %(message)s',
            datefmt='%Y-%m-%dT%H:%M:%S%z',
        ))
        LOGGER.addHandler(handler)
        LOGGER.setLevel(logging.INFO)
        LOGGER.propagate = False

    def unhandled(error_type, error, tb):
        if error is not None:
            error.__traceback__ = tb
            record_exception('unhandled application exception', error)
        if sys.__excepthook__:
            sys.__excepthook__(error_type, error, tb)

    sys.excepthook = unhandled
    LOGGER.info('application.start python=%s platform=%s', sys.version.split()[0], sys.platform)
    return directory / 'app.log'
