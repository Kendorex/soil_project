from tools.zmq_oneshot import build_oneshot_command


def test_zmq_launcher_uses_oneshot_without_interactive_or_verbose():
    command = build_oneshot_command(
        "/usr/bin/dotnet",
        "/home/user/ApsimX/bin/Release/net8.0/ApsimZMQServer.dll",
        "/home/user/ApsimOptimization/simulation/standart.apsimx",
        5555,
    )

    assert command == [
        "/usr/bin/dotnet",
        "/home/user/ApsimX/bin/Release/net8.0/ApsimZMQServer.dll",
        "--file",
        "/home/user/ApsimOptimization/simulation/standart.apsimx",
        "-P",
        "oneshot",
        "--port",
        "5555",
    ]
    assert "interactive" not in command
    assert "--verbose" not in command
