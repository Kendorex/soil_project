from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config


def build_oneshot_command(
    dotnet: str | Path,
    zmq_server_dll: str | Path,
    model_file: str | Path,
    port: int = 5555,
) -> list[str]:
    return [
        str(dotnet),
        str(zmq_server_dll),
        "--file",
        str(model_file),
        "-P",
        "oneshot",
        "--port",
        str(port),
    ]


def _configured_path(root: Path, value: str | Path) -> str:
    text = str(value)
    if text.startswith("/"):
        return text
    path = Path(text)
    return str(path if path.is_absolute() else root / path)


def _dotnet_path(configured: str) -> str:
    path = Path(configured)
    if path.is_file():
        return str(path)
    discovered = shutil.which(configured) or shutil.which(path.name)
    if discovered:
        return discovered
    raise FileNotFoundError(
        f"dotnet not found: configured path is {configured}. Run this launcher inside WSL."
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start one APSIMX ZeroMQ server in oneshot mode")
    parser.add_argument("--config", default="configs/optimizer_config.yaml")
    parser.add_argument("--file", dest="model_file", help="APSIMX file; defaults to apsim.base_template")
    parser.add_argument("--port", type=int, help="ZeroMQ port; defaults to apsim.zmq_port")
    parser.add_argument("--dry-run", action="store_true", help="print the command without starting APSIMX")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_config(args.config)
        root = config["_root"]
        apsim = config["apsim"]
        model = _configured_path(root, args.model_file or apsim["base_template"])
        dll = _configured_path(root, apsim["zmq_server_dll"])
        profile = str(apsim.get("zmq_profile", "oneshot"))
        if profile != "oneshot":
            raise ValueError("apsim.zmq_profile must be 'oneshot'")
        port = args.port if args.port is not None else int(apsim.get("zmq_port", 5555))
        command = build_oneshot_command(apsim["dotnet"], dll, model, port)

        print(shlex.join(command))
        if args.dry_run:
            return 0
        if not Path(model).is_file():
            raise FileNotFoundError(f"APSIMX model not found: {model}")
        if not Path(dll).is_file():
            raise FileNotFoundError(
                f"ApsimZMQServer.dll not found: {dll}. Update apsim.zmq_server_dll in {args.config}."
            )
        command[0] = _dotnet_path(str(apsim["dotnet"]))
        return subprocess.run(command, cwd=root, check=False).returncode
    except (KeyError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
