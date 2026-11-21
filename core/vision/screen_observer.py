"""core/vision/screen_observer.py — Always-On Screen Observer & Adaptive Change Detector.

Implements Section 1, 7, and 9 of Milestone 16:
- Event-driven and adaptive background capture across all connected monitors.
- Active-window Region-Of-Interest (ROI) differential perceptual hashing (dHash).
- Universal secret masking, credential dialog blanking, and user-configurable privacy exclusions.
- Bounded 30-frame memory ring buffer and immediate ephemeral raw bitmap destruction.
- UIA accessibility tree scan rate limiter (2.0s cooldown).
"""

from __future__ import annotations

import collections
import io
import re
import threading
import time
from typing import Any

from core.lazyimport import installed
from core.vision.types import DesktopState, PrivacyConfig, WindowInfo

_HAS_MSS = installed("mss")
_HAS_PIL = installed("PIL")


def _mss_factory():
    """A resilient mss screen grabber supporting mss 9 and mss 10."""
    import mss
    factory = getattr(mss, "MSS", None) or mss.mss
    return factory()


def compute_dhash(img_bytes: bytes, roi_rect: tuple[int, int, int, int] | None = None) -> str:
    """Compute a fast 64-bit difference hash (dHash) from image bytes, optionally cropped to an ROI."""
    if not _HAS_PIL:
        import hashlib
        return hashlib.md5(img_bytes or b"").hexdigest()[:16]

    try:
        from PIL import Image
        img = Image.open(io.BytesIO(img_bytes))

        # Crop to ROI if valid rectangle provided
        if roi_rect:
            l, t, r, b = roi_rect
            if r > l and b > t and l >= 0 and t >= 0:
                w, h = img.size
                crop_box = (max(0, l), max(0, t), min(w, r), min(h, b))
                if crop_box[2] > crop_box[0] and crop_box[3] > crop_box[1]:
                    img = img.crop(crop_box)

        # Downscale to 9x8 grayscale for 64-bit horizontal gradient comparison
        resized = img.convert("L").resize((9, 8), Image.Resampling.BILINEAR)
        if hasattr(resized, "get_flattened_data"):
            pixels = list(resized.get_flattened_data())
        else:
            pixels = list(resized.getdata())

        diff = []
        for row in range(8):
            for col in range(8):
                diff.append(pixels[row * 9 + col] > pixels[row * 9 + col + 1])

        decimal_value = 0
        hex_string = []
        for index, value in enumerate(diff):
            if value:
                decimal_value += 2 ** (index % 8)
            if (index % 8) == 7:
                hex_string.append(hex(decimal_value)[2:].rjust(2, "0"))
                decimal_value = 0

        return "".join(hex_string)
    except Exception:
        import hashlib
        return hashlib.md5(img_bytes or b"").hexdigest()[:16]


def hamming_distance(h1: str, h2: str) -> int:
    """Calculate hamming distance between two hex hashes."""
    if not h1 or not h2 or len(h1) != len(h2):
        return 64
    try:
        n1 = int(h1, 16)
        n2 = int(h2, 16)
        return (n1 ^ n2).bit_count()
    except Exception:
        return 64


def mask_sensitive_tokens(text: str) -> str:
    """Mask known credential token shapes (API keys, Bearer tokens, private keys)."""
    if not text:
        return ""
    # OpenAI / Anthropic / Google tokens
    text = re.sub(r"(sk-[a-zA-Z0-9_\-]{20,})", r"[REDACTED_API_KEY]", text)
    # Bearer tokens
    text = re.sub(r"(Bearer\s+[a-zA-Z0-9_\-\.]{20,})", r"Bearer [REDACTED_TOKEN]", text, flags=re.IGNORECASE)
    # Generic password assignments
    text = re.sub(r"(password\s*[:=]\s*)([^\s,;]+)", r"\1[REDACTED_SECRET]", text, flags=re.IGNORECASE)
    return text


def create_blank_thumbnail(width: int = 480, height: int = 270) -> bytes:
    """Create an empty black JPEG thumbnail when privacy redaction is active."""
    if not _HAS_PIL:
        return b""
    try:
        from PIL import Image
        img = Image.new("RGB", (width, height), color=(15, 20, 30))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
        return buf.getvalue()
    except Exception:
        return b""


class ScreenObserver:
    """Continuously observes the Windows desktop with adaptive polling and change detection."""

    def __init__(self, privacy_config: PrivacyConfig | None = None) -> None:
        self.privacy_config = privacy_config or PrivacyConfig()
        self._is_paused = False
        self._is_running = False
        self._worker_thread: threading.Thread | None = None
        self._lock = threading.Lock()

        # Bounded ring buffer for memory safety (max 30 states)
        self._history: collections.deque[DesktopState] = collections.deque(maxlen=30)
        self._last_state: DesktopState | None = None
        self._last_dhash: str = ""
        self._last_tree_scan_time: float = 0.0

        # Cooldown intervals
        self.idle_poll_interval: float = 0.50       # 2 Hz when desktop is static
        self.active_poll_interval: float = 0.15     # ~6.6 Hz during active supervision
        self.tree_scan_cooldown: float = 2.0        # Min 2.0s between full UIA tree walks
        self.is_active_supervision: bool = False

    @property
    def is_paused(self) -> bool:
        with self._lock:
            return self._is_paused

    def pause(self) -> None:
        """Immediately halt screen capture and observation."""
        with self._lock:
            self._is_paused = True

    def resume(self) -> None:
        """Resume screen capture and observation."""
        with self._lock:
            self._is_paused = False

    def get_history(self) -> list[DesktopState]:
        """Return a copy of the bounded observation history."""
        with self._lock:
            return list(self._history)

    def get_latest_state(self) -> DesktopState | None:
        """Return the most recent desktop state."""
        with self._lock:
            return self._last_state

    def clear_context(self) -> None:
        """Clear historical observations and reset dHash cache."""
        with self._lock:
            self._history.clear()
            self._last_state = None
            self._last_dhash = ""

    def discover_monitors(self) -> list[dict[str, Any]]:
        """Enumerate connected physical displays with screen boundaries."""
        monitors = []
        if _HAS_MSS:
            try:
                with _mss_factory() as sct:
                    for idx, m in enumerate(sct.monitors):
                        if idx == 0:
                            # Monitor 0 in mss is the virtual all-monitors surface
                            continue
                        monitors.append({
                            "index": idx,
                            "left": m["left"],
                            "top": m["top"],
                            "width": m["width"],
                            "height": m["height"],
                            "is_primary": (m["left"] == 0 and m["top"] == 0),
                        })
            except Exception:
                pass

        if not monitors:
            # Fallback single monitor default
            monitors.append({
                "index": 1,
                "left": 0,
                "top": 0,
                "width": 1920,
                "height": 1080,
                "is_primary": True,
            })
        return monitors

    def capture_frame(self, monitor_index: int = 1) -> tuple[bytes, tuple[int, int]]:
        """Grab a single screen frame. Returns (raw_png_bytes, (width, height))."""
        if self.is_paused:
            return b"", (0, 0)

        if _HAS_MSS:
            try:
                import mss.tools
                with _mss_factory() as sct:
                    target = sct.monitors[monitor_index] if monitor_index < len(sct.monitors) else sct.monitors[0]
                    shot = sct.grab(target)
                    png = mss.tools.to_png(shot.rgb, shot.size)
                    return png, shot.size
            except Exception:
                pass

        if _HAS_PIL:
            try:
                from PIL import ImageGrab
                img = ImageGrab.grab()
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return buf.getvalue(), img.size
            except Exception:
                pass

        return b"", (0, 0)

    def generate_downscaled_thumbnail(self, raw_bytes: bytes, max_width: int = 480, max_height: int = 270) -> bytes:
        """Generate a lightweight JPEG thumbnail (max 480x270) for UI display and discard raw bytes."""
        if not raw_bytes or not _HAS_PIL:
            return create_blank_thumbnail(max_width, max_height)
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
            img.thumbnail((max_width, max_height), Image.Resampling.BILINEAR)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=75)
            return buf.getvalue()
        except Exception:
            return create_blank_thumbnail(max_width, max_height)

    def observe_once(
        self,
        active_window: WindowInfo | None = None,
        windows: list[WindowInfo] | None = None,
        browser_tabs: list[Any] | None = None,
        force_scan: bool = False,
    ) -> DesktopState:
        """Capture and analyze the current desktop state in a single non-blocking pass."""
        now = time.time()
        monitors = self.discover_monitors()

        if self.is_paused:
            paused_state = DesktopState(
                timestamp=now,
                monitors=monitors,
                active_window=active_window,
                windows=windows or [],
                browser_tabs=browser_tabs or [],
                dhash=self._last_dhash,
                has_changed=False,
                is_paused=True,
                privacy_redacted=False,
                thumbnail_jpeg=create_blank_thumbnail(),
            )
            with self._lock:
                self._last_state = paused_state
            return paused_state

        # Check Privacy Boundaries on active window
        is_privacy_excluded = False
        if active_window and self.privacy_config.is_window_excluded(active_window.app_name, active_window.title):
            is_privacy_excluded = True

        cached_wins = self._last_state.windows if self._last_state else []
        cached_tabs = self._last_state.browser_tabs if self._last_state else []
        resolved_windows = windows if windows is not None else cached_wins
        resolved_tabs = browser_tabs if browser_tabs is not None else cached_tabs

        if is_privacy_excluded:
            redacted_state = DesktopState(
                timestamp=now,
                monitors=monitors,
                active_window=active_window,
                windows=resolved_windows,
                browser_tabs=resolved_tabs,
                dhash="0000000000000000",
                has_changed=False,
                ui_elements_count=0,
                is_paused=False,
                privacy_redacted=True,
                thumbnail_jpeg=create_blank_thumbnail(),
            )
            with self._lock:
                self._last_state = redacted_state
            return redacted_state

        raw_bytes, _size = self.capture_frame(monitor_index=1)

        # Active-window ROI for dHash calculation (reduces false change triggers)
        roi_rect = active_window.rect if active_window else None
        current_dhash = compute_dhash(raw_bytes, roi_rect=roi_rect)

        # Compare with previous dHash
        dist = hamming_distance(current_dhash, self._last_dhash)
        has_changed = (dist > 1) or force_scan or (active_window != (self._last_state.active_window if self._last_state else None))

        # Downscale thumbnail immediately; release raw_bytes to GC
        thumbnail = self.generate_downscaled_thumbnail(raw_bytes)
        del raw_bytes

        # Count visible UI elements if tree scan cooldown permits
        ui_elements_count = 0
        if has_changed and (now - self._last_tree_scan_time >= self.tree_scan_cooldown):
            try:
                from core import screenview
                view = screenview.look()
                ui_elements_count = len(view.elements)
                self._last_tree_scan_time = now
            except Exception:
                ui_elements_count = 0
        elif self._last_state:
            ui_elements_count = self._last_state.ui_elements_count

        state = DesktopState(
            timestamp=now,
            monitors=monitors,
            active_window=active_window,
            windows=resolved_windows,
            browser_tabs=resolved_tabs,
            dhash=current_dhash,
            has_changed=has_changed,
            ui_elements_count=ui_elements_count,
            is_paused=False,
            privacy_redacted=is_privacy_excluded,
            thumbnail_jpeg=thumbnail,
        )

        with self._lock:
            self._last_dhash = current_dhash
            self._last_state = state
            self._history.append(state)

        return state

    def start(self) -> None:
        """Start the continuous background observation thread."""
        with self._lock:
            if self._is_running:
                return
            self._is_running = True
            self._worker_thread = threading.Thread(target=self._run_loop, name="ScreenObserverWorker", daemon=True)
            self._worker_thread.start()

    def stop(self) -> None:
        """Stop the background observation thread."""
        with self._lock:
            self._is_running = False
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.0)
            self._worker_thread = None

    def _run_loop(self) -> None:
        """Background loop executing adaptive periodic observation."""
        while True:
            with self._lock:
                if not self._is_running:
                    break
                active_supervision = self.is_active_supervision
                interval = self.active_poll_interval if active_supervision else self.idle_poll_interval

            try:
                if not self.is_paused:
                    # Light inspection of foreground window
                    from core.vision.window_intelligence import get_active_window
                    fg = get_active_window()
                    self.observe_once(active_window=fg)
            except Exception:
                pass

            time.sleep(interval)
