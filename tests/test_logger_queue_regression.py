from src.utils.logger import DropOldestLogQueue


def test_log_queue_drops_oldest_records_when_full():
    log_queue = DropOldestLogQueue(maxsize=3)

    for index in range(7):
        log_queue.put(index)

    assert log_queue.qsize() == 3
    assert log_queue.dropped_count == 4
    assert [log_queue.get_nowait() for _ in range(3)] == [4, 5, 6]
