"""core/runtime/startup.py — Single-Instance Guard, OPTIMUS Desktop Shortcut & Auto-Start.

Implements Milestone 6:
- SingleInstanceGuard: Prevents duplicate OPTIMUS instances via Windows named mutex
  (ctypes.windll.kernel32.CreateMutexW) with cross-platform lockfile fallback.
- DesktopShortcutManager: Creates the canonical Desktop shortcut `OPTIMUS.lnk` with
  relative-to-install launcher resolution, correct working directory, and app icon.
- AutoStartManager: Manages user-level Windows auto-start registration (`OPTIMUS_AI`)
  under HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run (zero admin required,
  visible, toggleable, and verified against actual registry state).
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("optimus.runtime.startup")


class SingleInstanceGuard:
    """Process-wide single-instance guard preventing duplicate OPTIMUS launches."""

    def __init__(self, mutex_name: str = "Local\\OPTIMUS_Desktop_SingleInstance") -> None:
        self.mutex_name = mutex_name
        self._handle: Any = None
        self._lock_file: Path | None = None
        self._acquired = False

    def acquire(self) -> bool:
        """Attempt to acquire single-instance lock. Returns False if another instance holds it."""
        if self._acquired:
            return True

        if platform.system() == "Windows":
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.CreateMutexW(None, False, self.mutex_name)
                last_err = kernel32.GetLastError()
                # ERROR_ALREADY_EXISTS = 183
                if handle and last_err == 183:
                    kernel32.CloseHandle(handle)
                    return False
                if handle:
                    self._handle = handle
                    self._acquired = True
                    return True
            except Exception as exc:
                logger.debug("CreateMutexW fallback: %s", exc)

        # Cross-platform / fallback file lock
        lock_path = Path(tempfile.gettempdir()) / f"{self.mutex_name.replace('\\', '_')}.pid"
        try:
            if lock_path.exists():
                try:
                    existing_pid = int(lock_path.read_text(encoding="utf-8").strip())
                    import psutil
                    if existing_pid != os.getpid() and psutil.pid_exists(existing_pid):
                        return False
                except Exception:
                    pass
            lock_path.write_text(str(os.getpid()), encoding="utf-8")
            self._lock_file = lock_path
            self._acquired = True
            return True
        except Exception:
            return True

    def release(self) -> None:
        """Release mutex / lockfile cleanly on shutdown."""
        if self._handle is not None and platform.system() == "Windows":
            try:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None

        if self._lock_file is not None:
            try:
                self._lock_file.unlink(missing_ok=True)
            except Exception:
                pass
            self._lock_file = None
        self._acquired = False


class DesktopShortcutManager:
    """Creates and verifies the canonical `OPTIMUS` desktop shortcut."""

    SHORTCUT_NAME = "OPTIMUS.lnk"
    DESCRIPTION = "OPTIMUS — Autonomous Personal Agentic AI Assistant"

    @staticmethod
    def resolve_desktop_dir() -> Path:
        home = Path.home()
        if platform.system() == "Windows":
            try:
                import ctypes
                from ctypes import wintypes

                class _GUID(ctypes.Structure):
                    _fields_ = [
                        ("Data1", wintypes.DWORD),
                        ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD),
                        ("Data4", ctypes.c_ubyte * 8),
                    ]

                fid = _GUID(
                    0xB4BFCC3A, 0xDB2C, 0x424C,
                    (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41),
                )
                buf = ctypes.c_wchar_p()
                if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(buf)) == 0:
                    p = Path(buf.value)
                    ctypes.windll.ole32.CoTaskMemFree(buf)
                    if p.is_dir():
                        return p
            except Exception:
                pass

            try:
                import winreg
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
                ) as key:
                    val, _ = winreg.QueryValueEx(key, "Desktop")
                p = Path(os.path.expandvars(val))
                if p.is_dir():
                    return p
            except Exception:
                pass

        return home / "Desktop"

    @classmethod
    def create_shortcut(
        cls,
        *,
        project_root: Path | None = None,
        desktop_dir: Path | None = None,
        python_exe: Path | None = None,
    ) -> dict[str, Any]:
        """Create `OPTIMUS.lnk` on Windows Desktop without launching console windows."""
        root = (project_root or Path(__file__).resolve().parent.parent.parent).resolve()
        script = root / "main.py"
        if not script.exists():
            return {"success": False, "error": f"Launcher script not found: {script}"}

        py = Path(python_exe or sys.executable).resolve()
        pythonw = py.parent / "pythonw.exe"
        target_exe = str(pythonw if pythonw.exists() else py)

        ico = root / "config" / "optimus.ico"
        if not ico.exists():
            ico = root / "config" / "jarvis.ico"
        icon_loc = str(ico) if ico.exists() else f"{target_exe},0"

        desk = desktop_dir or cls.resolve_desktop_dir()
        desk.mkdir(parents=True, exist_ok=True)
        lnk_path = desk / cls.SHORTCUT_NAME

        if platform.system() != "Windows":
            desk_file = desk / "OPTIMUS.desktop"
            desk_file.write_text(
                f"[Desktop Entry]\nType=Application\nName=OPTIMUS\n"
                f"Comment={cls.DESCRIPTION}\nExec=\"{py}\" \"{script}\"\n"
                f"Path={root}\nTerminal=false\n",
                encoding="utf-8",
            )
            return {
                "success": True,
                "path": str(desk_file),
                "working_dir": str(root),
                "target": str(py),
            }

        try:
            from win32com.client import Dispatch  # type: ignore
            sh = Dispatch("WScript.Shell")
            sc = sh.CreateShortCut(str(lnk_path))
            sc.TargetPath = target_exe
            sc.Arguments = f'"{script}"'
            sc.WorkingDirectory = str(root)
            sc.Description = cls.DESCRIPTION
            sc.IconLocation = icon_loc
            sc.save()
        except ImportError:
            vbs = "\n".join([
                'Set ws = CreateObject("WScript.Shell")',
                f'Set sc = ws.CreateShortcut("{lnk_path}")',
                f'sc.TargetPath = "{target_exe}"',
                f'sc.Arguments = Chr(34) & "{script}" & Chr(34)',
                f'sc.WorkingDirectory = "{root}"',
                f'sc.Description = "{cls.DESCRIPTION}"',
                f'sc.IconLocation = "{icon_loc}"',
                "sc.Save",
            ])
            fd, tmp = tempfile.mkstemp(suffix=".vbs")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(vbs)
                proc = subprocess.Popen(
                    ["wscript.exe", "/nologo", tmp],
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
                )
                proc.wait(timeout=10)
            finally:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass

        return {
            "success": lnk_path.is_file(),
            "path": str(lnk_path),
            "target": target_exe,
            "arguments": f'"{script}"',
            "working_dir": str(root),
            "icon": icon_loc,
        }


class AutoStartManager:
    """User-level Windows Run registry auto-start manager (non-admin, visible, reversible)."""

    REG_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
    VALUE_NAME = "OPTIMUS_AI"
    LEGACY_VALUE_NAME = "JARVIS_AI"

    @classmethod
    def build_command(cls, *, project_root: Path | None = None, python_exe: Path | None = None) -> str:
        root = (project_root or Path(__file__).resolve().parent.parent.parent).resolve()
        script = root / "main.py"
        py = Path(python_exe or sys.executable).resolve()
        pythonw = py.parent / "pythonw.exe"
        exe = str(pythonw if pythonw.exists() else py)
        return f'"{exe}" "{script}"'

    @classmethod
    def is_enabled(cls) -> bool:
        try:
            if platform.system() == "Windows":
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, cls.REG_KEY_PATH, 0, winreg.KEY_READ) as key:
                    for name in (cls.VALUE_NAME, cls.LEGACY_VALUE_NAME):
                        try:
                            winreg.QueryValueEx(key, name)
                            return True
                        except FileNotFoundError:
                            continue
                return False
            elif platform.system() == "Darwin":
                return (Path.home() / "Library" / "LaunchAgents" / "com.optimus.assistant.plist").exists()
            return (Path.home() / ".config" / "autostart" / "optimus.desktop").exists()
        except Exception:
            return False

    @classmethod
    def enable(cls, *, project_root: Path | None = None, python_exe: Path | None = None) -> dict[str, Any]:
        cmd = cls.build_command(project_root=project_root, python_exe=python_exe)
        if platform.system() == "Windows":
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, cls.REG_KEY_PATH, 0, winreg.KEY_ALL_ACCESS) as key:
                # Clean up legacy key if migrating
                try:
                    winreg.DeleteValue(key, cls.LEGACY_VALUE_NAME)
                except FileNotFoundError:
                    pass
                winreg.SetValueEx(key, cls.VALUE_NAME, 0, winreg.REG_SZ, cmd)
                verified_val, _ = winreg.QueryValueEx(key, cls.VALUE_NAME)
            return {"enabled": verified_val == cmd, "registry_key": f"HKCU\\{cls.REG_KEY_PATH}\\{cls.VALUE_NAME}", "command": verified_val}
        desk_dir = Path.home() / ".config" / "autostart"
        desk_dir.mkdir(parents=True, exist_ok=True)
        desk = desk_dir / "optimus.desktop"
        desk.write_text(f"[Desktop Entry]\nType=Application\nName=OPTIMUS\nExec={cmd}\n", encoding="utf-8")
        return {"enabled": desk.exists(), "path": str(desk), "command": cmd}

    @classmethod
    def disable(cls) -> dict[str, Any]:
        if platform.system() == "Windows":
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, cls.REG_KEY_PATH, 0, winreg.KEY_ALL_ACCESS) as key:
                for name in (cls.VALUE_NAME, cls.LEGACY_VALUE_NAME):
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
            return {"enabled": cls.is_enabled()}
        desk = Path.home() / ".config" / "autostart" / "optimus.desktop"
        desk.unlink(missing_ok=True)
        return {"enabled": cls.is_enabled()}
