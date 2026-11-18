import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from dobotkit.printing import PrintJob, inspect_gcode, open_print_job


@pytest.fixture
def sliced_file(tmp_path: Path) -> Path:
    source = tmp_path / "part 1.GCODE"
    source.write_bytes(
        b"\xef\xbb\xbf; slicer header\r\n"
        b"T0\r\nM117 Printing test\r\nG90\r\nG00 X0 Y10 Z0.2 ; E2 is a comment\r\n"
        b"N91g01x2.5e.4\r\nG1 F1200\r\nG2 X5 Y0 I2 J1 E.6\r\n"
        b"G1 (retract) E-1\r\n; End of print\r\n"
    )
    return source


def test_inspect_real_slicer_syntax_without_changing_file(sliced_file: Path) -> None:
    original = sliced_file.read_bytes()
    assert inspect_gcode(sliced_file) == PrintJob(sliced_file.resolve(), 8, 4, 3)
    assert sliced_file.read_bytes() == original


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("model.stl", b"solid part", "sliced .gcode"),
        ("part.gcode", b"; only comments", "No G0/G1/G2/G3"),
        ("part.gcode", b"G1 X0\nM104 S0\x00\n", "line 2"),
        ("part.gcode", b"G1 X1\nthis is not printer code\n", "line 2"),
        ("part.gcode", b"G1 X1 Yabc", "line 1"),
        ("part.gcode", b"G1 F1200\nG1.1 X1", "No G0/G1/G2/G3"),
        ("part.gcode", b";@pause\nG1 X1", "Host directive"),
        ("part.gcode", b" @pause\nG1 X1", "Host directive"),
    ],
)
def test_reject_bad_file_before_host_launch(
    tmp_path: Path, name: str, content: bytes, message: str
) -> None:
    source = tmp_path / name
    source.write_bytes(content)
    with (
        patch("dobotkit.printing.subprocess.Popen") as launch,
        pytest.raises(ValueError, match=message),
    ):
        open_print_job(source, application=tmp_path / "not-installed")
    launch.assert_not_called()


def test_open_print_job_in_cura_on_mac(
    sliced_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "UltiMaker Cura.app"
    app.mkdir()
    monkeypatch.setattr("dobotkit.printing.sys.platform", "darwin")
    with patch("dobotkit.printing.subprocess.run") as launch:
        job = open_print_job(sliced_file, application=app)
    launch.assert_called_once_with(
        ["/usr/bin/open", "-a", str(app.resolve()), str(sliced_file.resolve())], check=True
    )
    assert job.extruder_moves == 3


def test_open_print_job_in_windows_host(
    sliced_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = tmp_path / "RepetierHost.exe"
    host.touch()
    host.chmod(0o755)
    monkeypatch.setattr("dobotkit.printing.sys.platform", "win32")
    with patch("dobotkit.printing.subprocess.Popen") as launch:
        job = open_print_job(sliced_file, application=host)
    launch.assert_called_once_with([str(host.resolve()), str(sliced_file.resolve())])
    assert job.path == sliced_file.resolve()


def test_handoff_reports_missing_host_and_launcher_errors(
    sliced_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "Cura.app"
    monkeypatch.setattr("dobotkit.printing.sys.platform", "darwin")
    with pytest.raises(FileNotFoundError):
        open_print_job(sliced_file, application=app)
    app.mkdir()
    with (
        patch(
            "dobotkit.printing.subprocess.run", side_effect=subprocess.CalledProcessError(1, "open")
        ),
        pytest.raises(subprocess.CalledProcessError),
    ):
        open_print_job(sliced_file, application=app)
