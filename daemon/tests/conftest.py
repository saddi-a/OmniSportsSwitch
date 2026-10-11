import pytest


@pytest.fixture(autouse=True)
def server_files_go_to_a_temporary_folder(tmp_path, monkeypatch):
    """A `Server` made in a test writes its files (sports.json, the feed recordings) into the test's own folder, never the real `logs/`."""
    import daemon.server
    monkeypatch.setattr(daemon.server, "LOG_DIR", tmp_path / "logs")
