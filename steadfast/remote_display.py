"""Virtual display (Xvfb) + VNC server for remote browser viewing.

Useful when you need a *headed* browser on a headless server — e.g. for a
manual-login flow where the operator drives the browser via VNC, then
Steadfast captures the resulting session cookies.

Requirements (must be installed on the host):
  - `Xvfb`
  - `x11vnc`

This module shells out to those binaries — no Python deps beyond stdlib.
"""

from __future__ import annotations

import asyncio
import os
import subprocess

from ._log import get_logger

log = get_logger("steadfast.remote_display")


class RemoteDisplay:
    """One virtual X display + a VNC server attached to it.

    Typical usage:

        rd = RemoteDisplay(display_num=99, vnc_port=5999)
        await rd.start()
        # launch a headed browser with env={'DISPLAY': rd.display}
        # operator connects VNC to localhost:vnc_port, logs in
        await rd.stop()
    """

    def __init__(
        self,
        display_num: int = 99,
        vnc_port: int = 5999,
        width: int = 1280,
        height: int = 900,
    ) -> None:
        self.display_num = display_num
        self.display = f":{display_num}"
        self.vnc_port = vnc_port
        self.width = width
        self.height = height
        self._xvfb: subprocess.Popen[bytes] | None = None
        self._x11vnc: subprocess.Popen[bytes] | None = None
        self._running = False

    async def start(self) -> None:
        """Launch Xvfb + x11vnc and mark the display ready.

        Idempotent — calling `start` twice on the same instance is a no-op.
        Raises `RuntimeError` if either subprocess fails to come up.
        """
        if self._running:
            return

        await self._cleanup_stale()

        # Xvfb: virtual framebuffer
        xvfb_cmd = [
            "Xvfb", self.display,
            "-screen", "0", f"{self.width}x{self.height}x24",
            "-ac",                  # disable access control
            "-nolisten", "tcp",     # security: no TCP
            "+extension", "RANDR",  # allow resize
        ]
        log.info(
            "Starting Xvfb",
            display=self.display,
            resolution=f"{self.width}x{self.height}",
        )
        self._xvfb = subprocess.Popen(
            xvfb_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        await asyncio.sleep(0.8)
        if self._xvfb.poll() is not None:
            raise RuntimeError(f"Xvfb failed to start (exit code {self._xvfb.returncode})")

        # x11vnc: VNC server pointing at our virtual display
        x11vnc_cmd = [
            "x11vnc",
            "-display", self.display,
            "-rfbport", str(self.vnc_port),
            "-nopw",          # no password (caller must firewall)
            "-forever",
            "-shared",
            "-noxdamage",
            "-noxfixes",
            "-noxrecord",
            "-wait", "20",
            "-defer", "10",
            "-localhost",     # only accept local connections
        ]
        log.info("Starting x11vnc", port=self.vnc_port)
        self._x11vnc = subprocess.Popen(
            x11vnc_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        await asyncio.sleep(0.5)
        if self._x11vnc.poll() is not None:
            raise RuntimeError(f"x11vnc failed to start (exit code {self._x11vnc.returncode})")

        self._running = True
        log.info("Remote display ready", display=self.display, vnc_port=self.vnc_port)

    async def stop(self) -> None:
        """Terminate x11vnc and Xvfb, then mark the display stopped.

        Sends SIGTERM with a 3-second grace window before SIGKILL.
        Idempotent — calling `stop` when not running is a no-op.
        """
        if not self._running:
            return
        for name, proc in [("x11vnc", self._x11vnc), ("Xvfb", self._xvfb)]:
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=2)
                    log.info(f"{name} stopped")
                except Exception as exc:
                    log.error(f"Error stopping {name}", error=str(exc))
        self._xvfb = None
        self._x11vnc = None
        self._running = False
        log.info("Remote display stopped")

    async def _cleanup_stale(self) -> None:
        """Kill any stale Xvfb/x11vnc on our display/port."""
        try:
            subprocess.run(
                ["pkill", "-f", f"Xvfb {self.display} "],
                capture_output=True, timeout=3,
            )
            subprocess.run(
                ["pkill", "-f", f"x11vnc.*-rfbport {self.vnc_port}"],
                capture_output=True, timeout=3,
            )
            lock_file = f"/tmp/.X{self.display_num}-lock"
            if os.path.exists(lock_file):
                os.remove(lock_file)
            socket_path = f"/tmp/.X11-unix/X{self.display_num}"
            if os.path.exists(socket_path):
                os.remove(socket_path)
        except Exception:
            # Best-effort cleanup. Any failure means we proceed and let
            # Xvfb/x11vnc startup surface the real error.
            pass

    @property
    def is_running(self) -> bool:
        """True iff both Xvfb and x11vnc are still alive."""
        if not self._running:
            return False
        if self._xvfb and self._xvfb.poll() is not None:
            self._running = False
            return False
        if self._x11vnc and self._x11vnc.poll() is not None:
            self._running = False
            return False
        return True

    @property
    def env(self) -> dict[str, str]:
        """Env vars needed to launch a process on this display."""
        return {"DISPLAY": self.display}
