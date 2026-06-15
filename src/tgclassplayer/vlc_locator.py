from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path


def find_vlc(saved_path: str | None = None) -> str | None:
    candidates: list[str] = []
    if saved_path:
        candidates.append(saved_path)

    found = shutil.which("vlc")
    if found:
        candidates.append(found)

    if sys.platform.startswith("win"):
        candidates.extend(
            [
                r"C:\Program Files\VideoLAN\VLC\vlc.exe",
                r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
            ]
        )
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
            base = os.environ.get(var)
            if base:
                candidates.append(str(Path(base) / "VideoLAN" / "VLC" / "vlc.exe"))
    elif sys.platform == "darwin":
        candidates.append("/Applications/VLC.app/Contents/MacOS/VLC")
    else:
        candidates.extend(["/usr/bin/vlc", "/usr/local/bin/vlc", "/snap/bin/vlc"])

    for item in candidates:
        if item and Path(item).exists():
            return str(Path(item))
    return None


def open_vlc_download_page() -> None:
    webbrowser.open("https://www.videolan.org/vlc/")


def launch_vlc(vlc_path: str, url: str, network_caching_ms: int = 1500) -> subprocess.Popen:
    """Abre o VLC apontando para o link de streaming local.

    O network-caching baixo mantém o streaming progressivo fluido sem travar.
    """
    args = [
        vlc_path,
        url,
        "--no-video-title-show",
        "--http-reconnect",
        f"--network-caching={network_caching_ms}",
        f"--file-caching={network_caching_ms}",
        f"--live-caching={network_caching_ms}",
    ]
    if sys.platform.startswith("win"):
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.Popen(args, creationflags=creationflags)
    return subprocess.Popen(args)
