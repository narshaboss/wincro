import logging

from src.utils.logger import ColoredFormatter, DropOldestLogQueue


def test_log_queue_drops_oldest_records_when_full():
    log_queue = DropOldestLogQueue(maxsize=3)

    for index in range(7):
        log_queue.put(index)

    assert log_queue.qsize() == 3
    assert log_queue.dropped_count == 4
    assert [log_queue.get_nowait() for _ in range(3)] == [4, 5, 6]


def test_colored_formatter_does_not_mutate_shared_log_record():
    record = logging.LogRecord(
        name="test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="message",
        args=(),
        exc_info=None,
    )

    rendered = ColoredFormatter("%(levelname)s %(message)s").format(record)

    assert "\x1b[33m" in rendered
    assert record.levelname == "WARNING"
