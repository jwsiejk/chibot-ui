from app.services.audio.container_sniffer import AudioContainerSniffer


def test_sniffer_detects_header_after_initial_window():
    sniffer = AudioContainerSniffer()

    # Initial chunk without a recognizable header.
    sniffer.feed(b"\x00" * sniffer.MAX_WINDOW)
    assert sniffer.detected is None

    # Second chunk where the header starts after the first 64 bytes overall.
    delayed_header_chunk = b"\x00" * 20 + sniffer.OGG_MAGIC + b"\x00" * 60
    detection = sniffer.feed(delayed_header_chunk)

    assert detection is not None
    assert detection.container == "ogg"
    assert detection.codec == "opus"
    assert detection.containerized is True
