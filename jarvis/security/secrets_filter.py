"""A logging filter that scrubs secrets from every log record.

The database already redacts before it *stores* a turn, but logs are written
from many places (router, tools, audio, API) and a mistake in one of them could
leak a key or a dictated password into ``logs/jarvis.log``.  This filter is a
second, defence-in-depth net: it runs on the formatted message of every record
attached to the ``jarvis`` logger and replaces anything that looks like a
credential before it reaches a handler.

It reuses :func:`memory.database.redact` so the rules cannot drift between the
database and the log.  The filter never raises: if a record cannot be
formatted it is passed through untouched.
"""

from __future__ import annotations

import logging

from memory.database import redact


class SecretsFilter(logging.Filter):
    """Redact credential-looking text from the message of each record."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            message = record.getMessage()
        except Exception:  # a broken %-format must not break logging itself
            return True
        cleaned = redact(message)
        if cleaned != message:
            # Replace both the template and the args so the record cannot be
            # re-formatted from the original, unsafe text.
            record.msg = cleaned
            record.args = ()
        return True


def install_secrets_filter(logger: logging.Logger) -> None:
    """Attach the filter to ``logger`` and every handler it owns.

    Attaching to the logger catches records as they are emitted; attaching to
    handlers catches records that were created before this call.  Either way the
    filter is cheap (a handful of regexes) and only rewrites when it actually
    finds something.
    """
    sf = SecretsFilter()
    logger.addFilter(sf)
    for handler in logger.handlers:
        handler.addFilter(sf)
    # A child logger (jarvis.audio, jarvis.tools, ...) propagates to this one,
    # and the filter on the parent is applied when the record arrives.
