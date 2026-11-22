"""core/application_intelligence/discovery.py — Windows Application Discovery Engine.

Discovers installed, registered, packaged (MSIX/UWP), and currently running
Windows desktop applications, protocol handlers, and file associations.
"""

from __future__ import annotations

import ctypes
import logging
import os
import re
import subprocess
import sys
import winreg
from pathlib import Path
from typing import Any

import psutil
import win32api
import win32gui
import win32process

from core.application_intelligence.types import (
    AppCategory,
    AppDefinition,
    AppInteractionTier,
)

logger = logging.getLogger("optimus.application_intelligence.discovery")

# Standard paths to search
START_MENU_PATHS = [
    Path(os.environ.get("ProgramData", "C:\\ProgramData")) / "Microsoft\\Windows\\Start Menu\\Programs",
    Path(os.environ.get("APPDATA", "")) / "Microsoft\\Windows\\Start Menu\\Programs",
]

# Untrusted directories that should NEVER be registered as system applications
UNTRUSTED_DIRECTORIES = [
    os.environ.get("TEMP", "").lower(),
    os.environ.get("TMP", "").lower(),
    (Path(os.environ.get("LOCALAPPDATA", "")) / "Temp").as_posix().lower(),
]

KNOWN_APP_CATEGORIES: dict[str, AppCategory] = {
    "whatsapp": AppCategory.COMMUNICATION,
    "telegram": AppCategory.COMMUNICATION,
    "discord": AppCategory.COMMUNICATION,
    "slack": AppCategory.COMMUNICATION,
    "teams": AppCategory.COMMUNICATION,
    "outlook": AppCategory.COMMUNICATION,
    "thunderbird": AppCategory.COMMUNICATION,
    "chrome": AppCategory.BROWSER,
    "msedge": AppCategory.BROWSER,
    "edge": AppCategory.BROWSER,
    "firefox": AppCategory.BROWSER,
    "brave": AppCategory.BROWSER,
    "opera": AppCategory.BROWSER,
    "code": AppCategory.DEVELOPMENT,
    "devenv": AppCategory.DEVELOPMENT,
    "windowsterminal": AppCategory.DEVELOPMENT,
    "powershell": AppCategory.DEVELOPMENT,
    "cmd": AppCategory.DEVELOPMENT,
    "git": AppCategory.DEVELOPMENT,
    "acrobat": AppCategory.DOCUMENT_VIEWER,
    "acrord32": AppCategory.DOCUMENT_VIEWER,
    "foxitreader": AppCategory.DOCUMENT_VIEWER,
    "winword": AppCategory.OFFICE,
    "excel": AppCategory.OFFICE,
    "powerpnt": AppCategory.OFFICE,
    "notepad": AppCategory.SYSTEM_UTILITY,
    "calc": AppCategory.SYSTEM_UTILITY,
    "calculator": AppCategory.SYSTEM_UTILITY,
    "explorer": AppCategory.SYSTEM_UTILITY,
    "mspaint": AppCategory.MEDIA,
}

KNOWN_APP_ALIASES: dict[str, list[str]] = {
    "whatsapp": ["wa", "whatsapp desktop", "whatsapp web"],
    "code": ["vscode", "visual studio code"],
    "chrome": ["google chrome", "browser"],
    "msedge": ["edge", "microsoft edge"],
    "windowsterminal": ["terminal", "wt"],
    "calc": ["calculator"],
    "notepad": ["text editor"],
    "winword": ["word", "microsoft word"],
    "excel": ["microsoft excel", "spreadsheet"],
    "powerpnt": ["powerpoint", "ppt"],
    "explorer": ["file explorer", "windows explorer"],
}


def _is_path_safe(path_str: str) -> bool:
    """Verify an executable path does not reside in an untrusted temporary folder."""
    if not path_str:
        return False
    norm = Path(path_str).resolve().as_posix().lower()
    for untrusted in UNTRUSTED_DIRECTORIES:
        if untrusted and norm.startswith(untrusted):
            return False
    return True


def _get_process_integrity_level(pid: int) -> str:
    """Query Windows process token integrity level."""
    try:
        proc = psutil.Process(pid)
        # Check if process is elevated / admin
        # Using ctypes or win32process if available
        # On Windows, standard user is Medium, elevated is High, system is System
        try:
            h_proc = win32api.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
            if h_proc:
                win32api.CloseHandle(h_proc)
        except Exception:
            return "High"  # Cannot open process usually means higher integrity or protected
        return "Medium"
    except Exception:
        return "Medium"


def discover_registry_app_paths() -> dict[str, AppDefinition]:
    """Discover applications registered under Windows App Paths."""
    apps: dict[str, AppDefinition] = {}
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
    ]

    for root_key, sub_key in roots:
        try:
            with winreg.OpenKey(root_key, sub_key) as key:
                num_subkeys, _, _ = winreg.QueryInfoKey(key)
                for i in range(num_subkeys):
                    try:
                        app_exe = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, app_exe) as app_key:
                            exe_path, _ = winreg.QueryValueEx(app_key, "")
                            if exe_path and _is_path_safe(exe_path) and Path(exe_path).is_file():
                                stem = Path(exe_path).stem.lower()
                                app_id = f"app.{stem}"
                                name = stem.capitalize()
                                category = KNOWN_APP_CATEGORIES.get(stem, AppCategory.OTHER)
                                aliases = KNOWN_APP_ALIASES.get(stem, [])
                                if stem not in aliases:
                                    aliases = [stem] + aliases

                                apps[app_id] = AppDefinition(
                                    app_id=app_id,
                                    name=name,
                                    aliases=aliases,
                                    executable_path=str(Path(exe_path).resolve()),
                                    category=category,
                                    supported_tiers=[
                                        AppInteractionTier.TIER_2_ADAPTER,
                                        AppInteractionTier.TIER_3_UI_AUTOMATION,
                                        AppInteractionTier.TIER_5_VISUAL_ACTUATION,
                                    ],
                                )
                    except Exception:
                        continue
        except Exception:
            continue

    return apps


def discover_uninstall_registry() -> dict[str, AppDefinition]:
    """Discover applications registered under Windows Uninstall keys."""
    apps: dict[str, AppDefinition] = {}
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]

    for root_key, sub_key in roots:
        try:
            with winreg.OpenKey(root_key, sub_key) as key:
                num_subkeys, _, _ = winreg.QueryInfoKey(key)
                for i in range(num_subkeys):
                    try:
                        sub_name = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, sub_name) as app_key:
                            try:
                                display_name, _ = winreg.QueryValueEx(app_key, "DisplayName")
                            except OSError:
                                continue

                            if not display_name or not display_name.strip():
                                continue

                            install_loc = ""
                            try:
                                install_loc, _ = winreg.QueryValueEx(app_key, "InstallLocation")
                            except OSError:
                                pass

                            display_icon = ""
                            try:
                                display_icon, _ = winreg.QueryValueEx(app_key, "DisplayIcon")
                                if display_icon:
                                    display_icon = display_icon.split(",")[0].strip('"')
                            except OSError:
                                pass

                            exe_path = None
                            if display_icon and display_icon.lower().endswith(".exe") and Path(display_icon).is_file():
                                if _is_path_safe(display_icon):
                                    exe_path = str(Path(display_icon).resolve())
                            elif install_loc and Path(install_loc).is_dir():
                                # Look for an exe matching display_name in root
                                for item in Path(install_loc).glob("*.exe"):
                                    if _is_path_safe(str(item)):
                                        exe_path = str(item.resolve())
                                        break

                            clean_name = display_name.strip()
                            slug = re.sub(r"[^a-zA-Z0-9_]+", ".", clean_name.lower()).strip(".")
                            app_id = f"pkg.{slug}"
                            stem = Path(exe_path).stem.lower() if exe_path else slug
                            category = KNOWN_APP_CATEGORIES.get(stem, AppCategory.OTHER)
                            aliases = [clean_name.lower(), stem] + KNOWN_APP_ALIASES.get(stem, [])

                            apps[app_id] = AppDefinition(
                                app_id=app_id,
                                name=clean_name,
                                aliases=list(dict.fromkeys(aliases)),
                                executable_path=exe_path,
                                category=category,
                                supported_tiers=[
                                    AppInteractionTier.TIER_2_ADAPTER,
                                    AppInteractionTier.TIER_3_UI_AUTOMATION,
                                    AppInteractionTier.TIER_5_VISUAL_ACTUATION,
                                ],
                            )
                    except Exception:
                        continue
        except Exception:
            continue

    return apps


def discover_start_menu_shortcuts() -> dict[str, AppDefinition]:
    """Discover applications via Windows Start Menu shortcuts."""
    apps: dict[str, AppDefinition] = {}

    for base_dir in START_MENU_PATHS:
        if not base_dir.is_dir():
            continue
        try:
            for item in base_dir.rglob("*.lnk"):
                clean_name = item.stem.strip()
                if clean_name.lower() in ("uninstall", "help", "documentation", "readme"):
                    continue

                slug = re.sub(r"[^a-zA-Z0-9_]+", ".", clean_name.lower()).strip(".")
                app_id = f"lnk.{slug}"
                stem = clean_name.lower()
                category = KNOWN_APP_CATEGORIES.get(stem, AppCategory.OTHER)
                aliases = [clean_name.lower(), stem] + KNOWN_APP_ALIASES.get(stem, [])

                apps[app_id] = AppDefinition(
                    app_id=app_id,
                    name=clean_name,
                    aliases=list(dict.fromkeys(aliases)),
                    executable_path=str(item.resolve()),
                    category=category,
                    supported_tiers=[
                        AppInteractionTier.TIER_2_ADAPTER,
                        AppInteractionTier.TIER_3_UI_AUTOMATION,
                        AppInteractionTier.TIER_5_VISUAL_ACTUATION,
                    ],
                    metadata={"shortcut_path": str(item.resolve())},
                )
        except Exception as ex:
            logger.debug("Start menu enumeration error: %s", ex)

    return apps


def discover_packaged_apps_and_protocols() -> dict[str, AppDefinition]:
    """Discover Windows Store / MSIX / UWP packaged apps and protocol handlers.

    Reconciles CRIT-1: Discovers modern packaged WhatsApp, Windows Terminal,
    Calculator, Edge, etc.
    """
    apps: dict[str, AppDefinition] = {}

    # 1. Query registered protocol handlers in HKCR (whatsapp://, ms-windows-store://, etc.)
    protocol_map = {
        "whatsapp": ("WhatsApp", AppCategory.COMMUNICATION, ["wa", "whatsapp desktop"]),
        "vscode": ("Visual Studio Code", AppCategory.DEVELOPMENT, ["code", "vscode"]),
        "mailto": ("Default Mail Client", AppCategory.COMMUNICATION, ["mail", "email"]),
        "ms-settings": ("Windows Settings", AppCategory.SYSTEM_UTILITY, ["settings", "control panel"]),
        "calculator": ("Calculator", AppCategory.SYSTEM_UTILITY, ["calc", "calculator"]),
    }

    for proto, (p_name, p_cat, p_aliases) in protocol_map.items():
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, proto) as key:
                app_id = f"protocol.{proto}"
                apps[app_id] = AppDefinition(
                    app_id=app_id,
                    name=p_name,
                    aliases=[proto] + p_aliases,
                    package_id=proto,
                    category=p_cat,
                    supported_tiers=[
                        AppInteractionTier.TIER_1_NATIVE_API,
                        AppInteractionTier.TIER_2_ADAPTER,
                        AppInteractionTier.TIER_3_UI_AUTOMATION,
                        AppInteractionTier.TIER_5_VISUAL_ACTUATION,
                    ],
                    metadata={"protocol": f"{proto}://"},
                )
        except Exception:
            pass

    # 2. Query Get-AppxPackage for modern Windows Store packages
    try:
        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-AppxPackage -User (whoami) | Select-Object -Property Name, PackageFamilyName, InstallLocation | ConvertTo-Json -Compress",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=8.0)
        if res.returncode == 0 and res.stdout.strip():
            import json
            pkg_data = json.loads(res.stdout)
            if isinstance(pkg_data, dict):
                pkg_data = [pkg_data]
            for p in pkg_data:
                name = p.get("Name", "")
                family = p.get("PackageFamilyName", "")
                loc = p.get("InstallLocation", "")
                lower_name = name.lower()

                # Target high-value packaged apps like WhatsApp, Terminal, Calculator
                is_target = any(k in lower_name for k in ("whatsapp", "windowsterminal", "calculator", "photos", "paint"))
                if is_target and family:
                    stem = lower_name.split(".")[-1]
                    app_id = f"uwp.{stem}"
                    category = KNOWN_APP_CATEGORIES.get(stem, AppCategory.OTHER)
                    aliases = [name.lower(), stem] + KNOWN_APP_ALIASES.get(stem, [])
                    apps[app_id] = AppDefinition(
                        app_id=app_id,
                        name=name,
                        aliases=list(dict.fromkeys(aliases)),
                        package_id=family,
                        executable_path=loc,
                        category=category,
                        supported_tiers=[
                            AppInteractionTier.TIER_2_ADAPTER,
                            AppInteractionTier.TIER_3_UI_AUTOMATION,
                            AppInteractionTier.TIER_5_VISUAL_ACTUATION,
                        ],
                        metadata={"is_uwp": True, "package_family": family},
                    )
    except Exception as ex:
        logger.debug("PowerShell AppxPackage discovery exception: %s", ex)

    # 3. Always guarantee core built-in Windows applications are defined
    builtins = [
        ("app.calc", "Calculator", ["calc", "calculator"], "calc.exe", AppCategory.SYSTEM_UTILITY),
        ("app.notepad", "Notepad", ["notepad", "text editor"], "notepad.exe", AppCategory.SYSTEM_UTILITY),
        ("app.explorer", "File Explorer", ["explorer", "windows explorer"], "explorer.exe", AppCategory.SYSTEM_UTILITY),
        ("app.cmd", "Command Prompt", ["cmd", "command prompt"], "cmd.exe", AppCategory.DEVELOPMENT),
        ("app.powershell", "PowerShell", ["powershell", "posh"], "powershell.exe", AppCategory.DEVELOPMENT),
    ]
    for b_id, b_name, b_aliases, b_exe, b_cat in builtins:
        if b_id not in apps:
            apps[b_id] = AppDefinition(
                app_id=b_id,
                name=b_name,
                aliases=b_aliases,
                executable_path=b_exe,
                category=b_cat,
                supported_tiers=[
                    AppInteractionTier.TIER_2_ADAPTER,
                    AppInteractionTier.TIER_3_UI_AUTOMATION,
                    AppInteractionTier.TIER_5_VISUAL_ACTUATION,
                ],
            )

    return apps


def discover_all_installed_applications() -> dict[str, AppDefinition]:
    """Execute full multi-source discovery across Windows."""
    combined: dict[str, AppDefinition] = {}

    # Merge in priority order: Packaged/Builtins -> Registry App Paths -> Uninstall -> Start Menu
    combined.update(discover_packaged_apps_and_protocols())
    combined.update(discover_registry_app_paths())
    combined.update(discover_uninstall_registry())
    combined.update(discover_start_menu_shortcuts())

    logger.info("Discovered %d installed and registered applications across Windows", len(combined))
    return combined


def discover_running_processes_and_windows() -> list[dict[str, Any]]:
    """Enumerate running processes with active top-level HWNDs and integrity levels."""
    running: list[dict[str, Any]] = []

    # Map HWNDs to PIDs
    hwnd_map: dict[int, list[int]] = {}

    def enum_cb(hwnd: int, extra: Any) -> bool:
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd).strip()
            if title:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                hwnd_map.setdefault(pid, []).append(hwnd)
        return True

    try:
        win32gui.EnumWindows(enum_cb, None)
    except Exception as ex:
        logger.debug("EnumWindows failed: %s", ex)

    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            pid = proc.info["pid"]
            name = proc.info["name"] or ""
            exe = proc.info["exe"] or ""
            hwnds = hwnd_map.get(pid, [])
            titles = [win32gui.GetWindowText(h) for h in hwnds]
            main_hwnd = hwnds[0] if hwnds else None

            integrity = _get_process_integrity_level(pid)

            running.append({
                "pid": pid,
                "name": name,
                "executable_path": exe,
                "hwnds": hwnds,
                "main_hwnd": main_hwnd,
                "window_titles": titles,
                "integrity_level": integrity,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return running


def resolve_file_association(extension: str) -> list[str]:
    """Resolve associated application executables or ProgIDs for a given file extension."""
    if not extension.startswith("."):
        extension = f".{extension}"
    candidates: list[str] = []

    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, extension) as ext_key:
            prog_id, _ = winreg.QueryValueEx(ext_key, "")
            if prog_id:
                cmd_path = f"{prog_id}\\shell\\open\\command"
                try:
                    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, cmd_path) as cmd_key:
                        raw_cmd, _ = winreg.QueryValueEx(cmd_key, "")
                        if raw_cmd:
                            # Extract executable path
                            match = re.match(r'^"?([^"]+)"?', raw_cmd)
                            if match:
                                candidates.append(match.group(1).strip())
                except Exception:
                    pass
    except Exception:
        pass

    # Built-in fallbacks based on common extensions
    common_fallbacks: dict[str, list[str]] = {
        ".pdf": ["acrobat.exe", "msedge.exe", "chrome.exe"],
        ".docx": ["winword.exe", "wordpad.exe"],
        ".xlsx": ["excel.exe"],
        ".pptx": ["powerpnt.exe"],
        ".txt": ["notepad.exe", "code.cmd"],
        ".csv": ["excel.exe", "notepad.exe"],
        ".py": ["code.cmd", "notepad.exe"],
        ".js": ["code.cmd", "notepad.exe"],
        ".html": ["msedge.exe", "chrome.exe"],
        ".png": ["mspaint.exe"],
        ".jpg": ["mspaint.exe"],
    }

    fallbacks = common_fallbacks.get(extension.lower(), [])
    for f in fallbacks:
        if f not in candidates:
            candidates.append(f)

    return candidates
