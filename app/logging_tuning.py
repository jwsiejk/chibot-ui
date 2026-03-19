import logging
import os


_WEBSOCKET_LOGGER_PREFIXES: tuple[str, ...] = (
    "websockets",
    "uvicorn.protocols.websockets",
    "wsproto",
)


def _ws_text_debug_enabled() -> bool:
    flag = os.getenv("ASKCHIP_WS_TEXT_DEBUG", "")
    return flag.lower() in ("1", "true", "yes", "on")


def _silence_logger(logger: logging.Logger) -> None:
    logger.setLevel(logging.WARNING)
    logger.propagate = False


def _matches_websocket_prefix(name: str) -> bool:
    return any(name.startswith(prefix) for prefix in _WEBSOCKET_LOGGER_PREFIXES)


def tune_logging_noise() -> None:
    """
    Reduce DEBUG spam from underlying websockets/uvicorn protocols,
    while keeping our own app.* loggers at DEBUG.
    """

    if _ws_text_debug_enabled():
        return

    noisy_loggers = [
        "websockets.server",
        "websockets.client",
        "websockets.protocol",
        "uvicorn.protocols.websockets.websockets_impl",
    ]
    for name in noisy_loggers:
        _silence_logger(logging.getLogger(name))

    logger_dict = getattr(logging.root.manager, "loggerDict", {})
    for name, candidate in logger_dict.items():
        if not isinstance(name, str) or not _matches_websocket_prefix(name):
            continue

        logger = candidate if isinstance(candidate, logging.Logger) else logging.getLogger(name)
        _silence_logger(logger)
