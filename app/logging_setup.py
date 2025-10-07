import logging, re, os

_RE_WS_TOKEN = re.compile(r'(ws_token=)[A-Za-z0-9_\-\.~%]+' )
def _redact(msg:str)->str:
    try:
        return _RE_WS_TOKEN.sub(r'\1<redacted>', msg or '')
    except Exception:
        return msg

class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = _redact(record.msg)
            # Some handlers use args tuple formatting
            if record.args:
                new_args = []
                for a in record.args:
                    if isinstance(a, str):
                        new_args.append(_redact(a))
                    else:
                        new_args.append(a)
                record.args = tuple(new_args)
        except Exception:
            pass
        return True


def _has_explicit_level(logger: logging.Logger) -> bool:
    """Return True if logger already has a non-default level set."""
    root = logging.getLogger()
    if logger is root:
        return logger.level != logging.WARNING
    return logger.level != logging.NOTSET


def install():
    lvl = os.environ.get("LOG_REDACT","1")
    if lvl not in ("0","false","no"):
        flt = RedactFilter()
        root = logging.getLogger()
        root.addFilter(flt)
        for h in root.handlers:
            h.addFilter(flt)

    if os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"):
        # On Render deployments ensure INFO logs surface unless already overridden.
        root = logging.getLogger()
        askchip_logger = logging.getLogger("askchip")
        if not _has_explicit_level(root):
            root.setLevel(logging.INFO)
        if not _has_explicit_level(askchip_logger):
            askchip_logger.setLevel(logging.INFO)
