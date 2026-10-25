"""Secrets must never reach a log file, even if a caller forgets to redact."""

from __future__ import annotations

import io
import logging

from security.secrets_filter import SecretsFilter, install_secrets_filter


def _capture(name: str):
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    install_secrets_filter(logger)
    return logger, buf


def test_filter_redacts_dictated_password():
    logger, buf = _capture("jarvis.test.secrets.password")
    logger.info("my password is hunter2secret")
    out = buf.getvalue()
    assert "hunter2" not in out and "[redacted]" in out


def test_filter_redacts_api_key_argument():
    logger, buf = _capture("jarvis.test.secrets.key")
    logger.info("using key %s now", "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ123456")
    out = buf.getvalue()
    assert "AIzaSy" not in out and "[redacted]" in out


def test_filter_leaves_ordinary_messages_alone():
    logger, buf = _capture("jarvis.test.secrets.normal")
    logger.info("open youtube for me")
    logger.info("schedule my 8am lab")
    out = buf.getvalue()
    assert "open youtube for me" in out
    assert "schedule my 8am lab" in out
    assert "[redacted]" not in out


def test_filter_does_not_raise_on_broken_record():
    f = SecretsFilter()

    class Broken:
        def getMessage(self):  # noqa: N802
            raise RuntimeError("boom")

    assert f.filter(Broken()) is True
