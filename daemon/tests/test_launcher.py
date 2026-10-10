import pathlib

from launcher import launcher


def make_project(tmp_path):
    (tmp_path / "daemon").mkdir()
    (tmp_path / "daemon" / "server.py").write_text("")
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    (tmp_path / ".venv" / "Scripts" / "python.exe").write_text("")
    return tmp_path


def test_the_project_folder_is_found_from_the_exe_folder_or_below(tmp_path):
    root = make_project(tmp_path)
    (root / "dist").mkdir()
    assert launcher.find_root(root) == root
    assert launcher.find_root(root / "dist") == root


def test_no_project_folder_means_none(tmp_path):
    assert launcher.find_root(tmp_path) is None


def test_the_daemon_command_uses_the_projects_own_python(tmp_path):
    root = make_project(tmp_path)
    assert launcher.daemon_command(root) == [str(root / ".venv" / "Scripts" / "python.exe"), "-m", "daemon.server"]


def test_the_edge_command_matches_the_start_script(tmp_path):
    root = make_project(tmp_path)
    command = launcher.edge_command(root, "msedge.exe", local_app_data=str(tmp_path / "local"))
    assert command[0] == "msedge.exe"
    assert "--remote-debugging-port=9222" in command
    assert f"--load-extension={root / 'extension'}" in command
    assert any(a.startswith("--user-data-dir=") and a.endswith("edge-profile") for a in command)
    script = (pathlib.Path(__file__).parents[2] / "scripts" / "start_test_edge.ps1").read_text(encoding="utf-8")
    for flag in ("--remote-debugging-port=9222", "--disable-features=DisableLoadExtensionCommandLineSwitch",
                 "--no-first-run", "--restore-last-session"):
        assert flag in script and flag in command


def test_a_closed_port_is_reported_closed():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    assert launcher.port_open(free) is False


def test_the_listening_process_is_read_from_netstat(monkeypatch):
    class Result:
        stdout = ("  TCP    127.0.0.1:8765         0.0.0.0:0              LISTENING       4242\n"
                  "  TCP    0.0.0.0:9222           0.0.0.0:0              LISTENING       777\n")

    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: Result)
    assert launcher.listening_pid(8765) == 4242
    assert launcher.listening_pid(9222) == 777
    assert launcher.listening_pid(1234) is None


def test_the_icon_image_can_be_drawn():
    assert launcher.make_icon_image().size == (64, 64)
