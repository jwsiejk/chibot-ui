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
        # Drop them to WARNING and disable propagation so per-frame
        # <TEXT>/<BINARY> debug lines never bubble up to the root logger,
        # even when the root is configured for DEBUG.
        logger.setLevel(logging.WARNING)
        logger.propagate = False
