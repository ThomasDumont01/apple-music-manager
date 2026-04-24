"""Preview mixin — audio preview in background thread."""

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from music_manager.ui.screens._protocol import MenuScreenProto

    _MixinBase = MenuScreenProto
else:
    _MixinBase = object


class PreviewMixin(_MixinBase):
    """Audio preview methods for MenuScreen."""

    def _play_preview(self, url: str) -> None:
        """Play a 30s Deezer preview in background thread."""
        import threading  # noqa: PLC0415

        # Kill previous preview if still playing
        if self._preview_proc is not None:
            try:
                self._preview_proc.kill()
            except OSError:
                pass
            self._preview_proc = None

        def _play() -> None:
            import tempfile  # noqa: PLC0415

            tmp_path = ""
            try:
                from music_manager.services.resolver import http_get  # noqa: PLC0415

                response = http_get(url, timeout=10)
                if response.status_code == 200:
                    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
                    tmp.write(response.content)
                    tmp.close()
                    tmp_path = tmp.name
                    self._preview_proc = subprocess.Popen(
                        ["afplay", tmp_path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    self._preview_proc.wait()
            except (OSError, Exception):  # noqa: BLE001
                pass
            finally:
                self._preview_proc = None
                if tmp_path:
                    import os  # noqa: PLC0415

                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass

        threading.Thread(target=_play, daemon=True).start()

    def _preview_cover(self) -> None:
        """Open cover URL in browser for preview."""
        if not self._fix_albums or self._fix_album_idx >= len(self._fix_albums):
            return
        cover_url = self._fix_albums[self._fix_album_idx].cover_url
        if cover_url:
            import webbrowser  # noqa: PLC0415

            webbrowser.open(cover_url)
