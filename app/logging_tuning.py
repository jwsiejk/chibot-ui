import logging


def tune_logging_noise() -> None:
    """
    Reduce DEBUG spam from underlying websockets/uvicorn protocols,
    while keeping our own app.* loggers at DEBUG.
    """
    noisy_loggers = [
        "websockets.server",
        "websockets.client",
        "websockets.protocol",
        "uvicorn.protocols.websockets.websockets_impl",
    ]
    for name in noisy_loggers:
        logger = logging.getLogger(name)
        # Drop them to INFO so per-frame <TEXT>/<BINARY> lines disappear
        logger.setLevel(logging.INFO)
