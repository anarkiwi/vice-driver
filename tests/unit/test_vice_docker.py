"""Unit tests for vice_driver.vice_docker — covers the pure-python
``x64sc_args()`` command-line builder, the ``DiskMount.docker_arg()``
helper, and the container-lifecycle methods (start/stop/logs/
is_running) with ``subprocess.run`` mocked. No real Docker invocation."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from vice_driver import vice_docker
from vice_driver.vice_docker import DiskMount, ViceContainer, ViceContainerError


def test_x64sc_args_defaults() -> None:
    args = ViceContainer().x64sc_args()
    assert "-default" in args
    assert "-binarymonitor" in args
    assert "-binarymonitoraddress" in args
    assert "ip4://0.0.0.0:6502" in args
    assert "-sounddev" in args
    assert args[args.index("-sounddev") + 1] == "dummy"
    # warp=True is the default
    assert "-warp" in args
    # No autostart / silent / sid extras by default
    assert "-autostart" not in args
    assert "-silent" not in args
    assert "-sidextra" not in args
    assert "+drive8truedrive" not in args  # truedrive=True is default


def test_x64sc_args_autostart_path_threaded_through() -> None:
    args = ViceContainer(autostart="/work/foo.d64").x64sc_args()
    assert args[args.index("-autostart") + 1] == "/work/foo.d64"


def test_x64sc_args_silent_and_no_warp() -> None:
    args = ViceContainer(warp=False, silent=True).x64sc_args()
    assert "-warp" not in args
    assert "-silent" in args


def test_x64sc_args_disables_truedrive_when_requested() -> None:
    args = ViceContainer(truedrive=False).x64sc_args()
    assert "+drive8truedrive" in args


def test_x64sc_args_sound_dump_passes_path_via_soundarg() -> None:
    args = ViceContainer(
        sounddev="dump",
        sounddump_path="/work/sound.dump",
    ).x64sc_args()
    assert args[args.index("-sounddev") + 1] == "dump"
    assert "-soundarg" in args
    assert args[args.index("-soundarg") + 1] == "/work/sound.dump"


def test_x64sc_args_sound_dump_without_path_omits_soundarg() -> None:
    # Missing sounddump_path should NOT inject a None or crash.
    args = ViceContainer(sounddev="dump").x64sc_args()
    assert "-soundarg" not in args


def test_x64sc_args_2sid_emits_sid2_address_only() -> None:
    args = ViceContainer(sid_extras=1, sid2_address=0xD420).x64sc_args()
    assert "-sidextra" in args
    assert args[args.index("-sidextra") + 1] == "1"
    assert "-sid2address" in args
    assert args[args.index("-sid2address") + 1] == "0xd420"
    assert "-sid3address" not in args


def test_x64sc_args_3sid_emits_sid3_address() -> None:
    args = ViceContainer(sid_extras=2, sid2_address=0xD420, sid3_address=0xD440).x64sc_args()
    assert args[args.index("-sidextra") + 1] == "2"
    assert args[args.index("-sid3address") + 1] == "0xd440"


def test_x64sc_args_extra_args_appended() -> None:
    args = ViceContainer(extra_args=["-myextra", "1"]).x64sc_args()
    # Extras come after the rest.
    assert args[-2:] == ["-myextra", "1"]


def test_x64sc_args_extra_args_after_autostart() -> None:
    # Both autostart and extra_args set: extra_args still trail autostart.
    args = ViceContainer(autostart="/work/a.d64", extra_args=["-x", "1"]).x64sc_args()
    autostart_i = args.index("-autostart")
    extras_i = args.index("-x")
    assert extras_i > autostart_i


def test_disk_mount_docker_arg_readonly() -> None:
    m = DiskMount("/host/path.d64", "/work/path.d64", read_only=True)
    flag = m.docker_arg()
    assert flag[0] == "-v"
    # second element is "<abs-host>:/work/path.d64:ro"
    spec = flag[1]
    assert spec.endswith(":/work/path.d64:ro")


def test_disk_mount_docker_arg_writable_default() -> None:
    m = DiskMount("/host/p.d64", "/work/p.d64")
    spec = m.docker_arg()[1]
    assert spec.endswith(":/work/p.d64:rw")


def test_vice_container_generates_unique_name_when_unset() -> None:
    a = ViceContainer()
    b = ViceContainer()
    assert a.name is not None
    assert b.name is not None
    # Two distinct constructions should produce two distinct names.
    assert a.name != b.name


def test_vice_container_name_respected_when_set() -> None:
    c = ViceContainer(name="my-container")
    assert c.name == "my-container"


# ---- lifecycle tests with subprocess + which mocked -------------------------


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> Any:
    """Build a CompletedProcess-shaped object the start/stop methods accept."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def mock_docker(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """Patch shutil.which + subprocess.run so the container lifecycle
    methods run end-to-end without invoking Docker. Returns a dict whose
    'calls' key collects every subprocess.run argv for inspection."""
    monkeypatch.setattr(vice_docker.shutil, "which", lambda _b: "docker")

    state: dict[str, list] = {"calls": [], "responses": []}

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        state["calls"].append(cmd)
        if state["responses"]:
            return state["responses"].pop(0)
        return _completed(stdout="fake-container-id-abcdef\n")

    monkeypatch.setattr(vice_docker.subprocess, "run", fake_run)
    return state


def test_start_invokes_docker_run_with_expected_args(mock_docker: dict[str, list]) -> None:
    c = ViceContainer(name="probe", binmon_port=6712, autostart="/work/disk.d64")
    c.start()
    assert c.container_id == "fake-container-id-abcdef"
    [run_cmd] = mock_docker["calls"]
    assert run_cmd[:4] == ["docker", "run", "-d", "--rm"]
    assert "probe" in run_cmd
    assert "6712:6502" in run_cmd
    # x64sc_args are appended after the image name.
    assert "-autostart" in run_cmd
    assert "/work/disk.d64" in run_cmd
    # No --entrypoint override by default.
    assert "--entrypoint" not in run_cmd


def test_start_passes_entrypoint_override(mock_docker: dict[str, list]) -> None:
    c = ViceContainer(
        image="anarkiwi/headlessvice:latest",
        entrypoint="x64sc",
    )
    c.start()
    [run_cmd] = mock_docker["calls"]
    # --entrypoint must precede the image so docker treats it as a
    # ``docker run`` flag rather than passing it as container argv.
    image_idx = run_cmd.index("anarkiwi/headlessvice:latest")
    ep_idx = run_cmd.index("--entrypoint")
    assert ep_idx < image_idx
    assert run_cmd[ep_idx + 1] == "x64sc"
    # The trailing flags must still be the x64sc_args, not consumed by the
    # entrypoint pair.
    assert "-binarymonitor" in run_cmd[image_idx + 1 :]


def test_start_omits_entrypoint_when_none(mock_docker: dict[str, list]) -> None:
    ViceContainer().start()
    [run_cmd] = mock_docker["calls"]
    assert "--entrypoint" not in run_cmd


def test_start_passes_disk_mounts(mock_docker: dict[str, list]) -> None:
    c = ViceContainer(
        mounts=[DiskMount("/host/x.d64", "/work/x.d64", read_only=True)],
    )
    c.start()
    [run_cmd] = mock_docker["calls"]
    # The -v <mount> pair must appear in the docker argv.
    assert "-v" in run_cmd
    spec_i = run_cmd.index("-v") + 1
    assert run_cmd[spec_i].endswith(":/work/x.d64:ro")


def test_start_refuses_double_start(mock_docker: dict[str, list]) -> None:
    c = ViceContainer()
    c.start()
    with pytest.raises(ViceContainerError, match="already started"):
        c.start()


def test_start_raises_when_docker_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vice_docker.shutil, "which", lambda _b: None)
    c = ViceContainer()
    with pytest.raises(ViceContainerError, match="not on PATH"):
        c.start()


def test_start_raises_on_docker_run_failure(
    monkeypatch: pytest.MonkeyPatch, mock_docker: dict[str, list]
) -> None:
    def fail_run(cmd, **kwargs):  # noqa: ARG001
        mock_docker["calls"].append(cmd)
        raise subprocess.CalledProcessError(
            returncode=125, cmd=cmd, output="", stderr="port in use"
        )

    monkeypatch.setattr(vice_docker.subprocess, "run", fail_run)
    c = ViceContainer()
    with pytest.raises(ViceContainerError, match="port in use"):
        c.start()


def test_pull_runs_pull_before_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vice_docker.shutil, "which", lambda _b: "docker")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        calls.append(cmd)
        return _completed(stdout="cid\n")

    monkeypatch.setattr(vice_docker.subprocess, "run", fake_run)
    c = ViceContainer(pull=True)
    c.start()
    assert calls[0][:2] == ["docker", "pull"]
    assert calls[1][:2] == ["docker", "run"]


def test_stop_invokes_docker_stop_and_clears_id(mock_docker: dict[str, list]) -> None:
    c = ViceContainer()
    c.start()
    c.stop()
    # First call was `docker run`, second is `docker stop`.
    assert mock_docker["calls"][-1][:3] == ["docker", "stop", "-t"]
    assert c.container_id is None


def test_stop_is_noop_when_not_started(mock_docker: dict[str, list]) -> None:
    c = ViceContainer()
    c.stop()
    assert mock_docker["calls"] == []


def test_is_running_returns_false_when_not_started(mock_docker: dict[str, list]) -> None:
    assert ViceContainer().is_running() is False
    assert mock_docker["calls"] == []  # no docker call when no container_id


def test_is_running_parses_inspect_output(mock_docker: dict[str, list]) -> None:
    c = ViceContainer()
    c.start()
    mock_docker["responses"].append(_completed(stdout="true\n"))
    assert c.is_running() is True
    mock_docker["responses"].append(_completed(stdout="false\n"))
    assert c.is_running() is False


def test_logs_returns_concatenated_stdout_and_stderr(mock_docker: dict[str, list]) -> None:
    c = ViceContainer()
    c.start()
    mock_docker["responses"].append(_completed(stdout="out\n", stderr="err\n"))
    assert c.logs() == "out\nerr\n"


def test_logs_empty_when_not_started() -> None:
    assert ViceContainer().logs() == ""


def test_context_manager_starts_and_stops(mock_docker: dict[str, list]) -> None:
    with ViceContainer() as c:
        assert c.container_id is not None
    # Container ID cleared after stop.
    assert c.container_id is None
    # Both run + stop were issued.
    cmds = [c[1] for c in mock_docker["calls"]]
    assert "run" in cmds
    assert "stop" in cmds
