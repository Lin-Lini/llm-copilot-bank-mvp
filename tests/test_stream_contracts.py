from apps.backend.app.api.v1.routes.copilot import _stream_chan as backend_stream_chan
from apps.worker.app.main import _stream_chan as worker_stream_chan


def test_backend_and_worker_use_same_stream_channel():
    task_id = 'task-123'
    assert backend_stream_chan(task_id) == worker_stream_chan(task_id)
    assert backend_stream_chan(task_id) == 'copilot:stream:task-123'