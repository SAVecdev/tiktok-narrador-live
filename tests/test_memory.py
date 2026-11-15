from narrador.memory import MemoryStore


def test_activity_and_facts_persist_and_can_be_cleared(tmp_path):
    path = tmp_path / 'memory.sqlite3'
    memory = MemoryStore(path)
    memory.set_activity('Jugando Minecraft')
    memory.set_facts('Estoy construyendo una granja\nNo spoilers')
    assert MemoryStore(path).context() == {
        'activity': 'Jugando Minecraft', 'facts': 'No spoilers\nEstoy construyendo una granja'
    }
    memory.clear()
    assert not path.exists()


def test_join_queue_is_fifo_deduplicated_and_marked_after_speech(tmp_path):
    memory = MemoryStore(tmp_path / 'memory.sqlite3')
    memory.enqueue_join('room-1', 'viewer-1', 'Ana')
    memory.enqueue_join('room-1', 'viewer-2', 'Luis')
    memory.enqueue_join('room-1', 'viewer-1', 'Ana duplicada')
    count, first = memory.next_pending_join('room-1')
    assert count == 2 and first == ('viewer-1', 'Ana')
    memory.mark_greeted('room-1', 'viewer-1')
    assert memory.pending_joins('room-1') == [('viewer-2', 'Luis')]
    memory.keep_room_only('room-2')
    assert memory.pending_joins('room-1') == []


def test_old_greeted_records_are_pruned_without_dropping_pending(tmp_path):
    memory = MemoryStore(tmp_path / 'memory.sqlite3')
    memory.enqueue_join('old-room', 'done', 'Ana')
    memory.mark_greeted('old-room', 'done')
    memory.enqueue_join('old-room', 'pending', 'Luis')
    with memory._connect() as db:
        db.execute("UPDATE joins SET created='2000-01-01T00:00:00+00:00'")
    memory.prune_greeted(days=90)
    assert memory.pending_joins('old-room') == [('pending', 'Luis')]
