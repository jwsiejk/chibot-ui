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

def install():
    lvl = os.environ.get("LOG_REDACT","1")
    if lvl not in ("0","false","no"):
        flt = RedactFilter()
        root = logging.getLogger()
        root.addFilter(flt)
        for h in root.handlers:
            h.addFilter(flt)
