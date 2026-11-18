"""core/windows_runtime/service.py — Windows Privileged Service & Daemon Lifecycle Manager.

Implements Section 8 of Milestone 11:
- Native Windows Service integration via pywin32 `win32serviceutil.ServiceFramework`.
- Resilient background daemon mode for instant start without requiring service registration.
- Service installation, status query, start, stop, and clean uninstallation.
- Automatic recovery and graceful shutdown handling.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from core.windows_runtime.broker import PrivilegedExecutionBroker
from core.windows_runtime.ipc import PIPE_NAME, NamedPipeServer

logger = logging.getLogger("optimus.windows_runtime.service")

SERVICE_NAME = "OptimusPrivilegedService"
SERVICE_DISPLAY_NAME = "OPTIMUS Privileged Execution Service"
SERVICE_DESCRIPTION = "Provides secure, authenticated Windows administrator-capable runtime operations for OPTIMUS."


class WindowsPrivilegedDaemon:
    """Standalone background daemon running the Named Pipe server and Execution Broker."""

    def __init__(self, pipe_name: str = PIPE_NAME) -> None:
        self.broker = PrivilegedExecutionBroker()
        self.server = NamedPipeServer(request_handler=self.broker.execute_request, pipe_name=pipe_name)
        self._running = False

    def start(self) -> None:
        """Start the background pipe server."""
        self._running = True
        self.server.start()
        logger.info("Optimus Privileged Execution Daemon active on %s", PIPE_NAME)

    def stop(self) -> None:
        """Stop the background pipe server."""
        self._running = False
        self.server.stop()
        logger.info("Optimus Privileged Execution Daemon stopped.")

    def run_forever(self) -> None:
        """Run continuously in the foreground (for service worker or standalone process)."""
        self.start()
        try:
            while self._running:
                time.sleep(1.0)
        except (KeyboardInterrupt, SystemExit):
            self.stop()


# ── Native Windows Service Framework Integration ────────────────────────────

if os.name == "nt":
    try:
        import servicemanager
        import win32event
        import win32service
        import win32serviceutil

        class OptimusWindowsService(win32serviceutil.ServiceFramework):
            _svc_name_ = SERVICE_NAME
            _svc_display_name_ = SERVICE_DISPLAY_NAME
            _svc_description_ = SERVICE_DESCRIPTION

            def __init__(self, args):
                super().__init__(args)
                self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
                self.daemon = WindowsPrivilegedDaemon()

            def SvcStop(self):
                self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
                win32event.SetEvent(self.hWaitStop)
                self.daemon.stop()

            def SvcDoRun(self):
                servicemanager.LogMsg(
                    servicemanager.EVENTLOG_INFORMATION_TYPE,
                    servicemanager.PYS_SERVICE_STARTED,
                    (self._svc_name_, ""),
                )
                self.daemon.start()
                win32event.WaitForSingleObject(self.hWaitStop, win32event.INFINITE)
                self.daemon.stop()

    except Exception:  # noqa: BLE001
        OptimusWindowsService = None
else:
    OptimusWindowsService = None


# ── Service Lifecycle & Management CLI ───────────────────────────────────────

def get_service_status() -> dict[str, str]:
    """Query current Windows service status."""
    if os.name != "nt":
        return {"installed": "false", "status": "NON_WINDOWS"}

    try:
        import win32service
        scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
        try:
            svc_handle = win32service.OpenService(scm, SERVICE_NAME, win32service.SERVICE_QUERY_STATUS)
            status_info = win32service.QueryServiceStatus(svc_handle)
            win32service.CloseServiceHandle(svc_handle)
            st_code = status_info[1]
            status_map = {
                win32service.SERVICE_RUNNING: "RUNNING",
                win32service.SERVICE_STOPPED: "STOPPED",
                win32service.SERVICE_START_PENDING: "STARTING",
                win32service.SERVICE_STOP_PENDING: "STOPPING",
            }
            return {"installed": "true", "status": status_map.get(st_code, "UNKNOWN")}
        except Exception:  # noqa: BLE001
            return {"installed": "false", "status": "NOT_INSTALLED"}
        finally:
            win32service.CloseServiceHandle(scm)
    except Exception as ex:  # noqa: BLE001
        return {"installed": "unknown", "status": "ERROR", "error": str(ex)}


def install_service() -> bool:
    """Register the OPTIMUS privileged service (requires Administrator elevation)."""
    if os.name != "nt":
        print("Service installation is only supported on Windows.")
        return False

    script_path = str(Path(__file__).resolve())
    python_exe = sys.executable

    cmd = [python_exe, script_path, "--startup", "auto", "install"]
    res = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if res.returncode == 0:
        logger.info("Successfully registered Windows Service: %s", SERVICE_NAME)
        return True
    else:
        logger.error("Failed to install service: %s", res.stderr or res.stdout)
        return False


def start_service() -> bool:
    """Start the registered Windows service."""
    if os.name != "nt":
        return False
    try:
        import win32serviceutil
        win32serviceutil.StartService(SERVICE_NAME)
        return True
    except Exception as ex:  # noqa: BLE001
        logger.error("Failed to start service %s: %s", SERVICE_NAME, ex)
        return False


def stop_service() -> bool:
    """Stop the running Windows service."""
    if os.name != "nt":
        return False
    try:
        import win32serviceutil
        win32serviceutil.StopService(SERVICE_NAME)
        return True
    except Exception as ex:  # noqa: BLE001
        logger.error("Failed to stop service %s: %s", SERVICE_NAME, ex)
        return False


def uninstall_service() -> bool:
    """Unregister the Windows service."""
    if os.name != "nt":
        return False
    try:
        import win32serviceutil
        win32serviceutil.RemoveService(SERVICE_NAME)
        return True
    except Exception as ex:  # noqa: BLE001
        logger.error("Failed to remove service %s: %s", SERVICE_NAME, ex)
        return False


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--daemon":
        # Run background daemon directly
        daemon = WindowsPrivilegedDaemon()
        daemon.run_forever()
    elif os.name == "nt" and OptimusWindowsService is not None:
        import win32serviceutil
        win32serviceutil.HandleCommandLine(OptimusWindowsService)
    else:
        print("Usage: python service.py [--daemon | install | start | stop | remove]")
