# Year: 2026
import os
import tempfile
import logging
import wx

logger = logging.getLogger(__name__)


class SystemPlayerLauncher:
    """Открывает треки, альбомы, плейлисты и исполнителей
    во внешнем системном плеере через временные M3U-файлы.
    """

    def __init__(self, config_manager=None, frame=None):
        self.config_manager = config_manager
        self.frame = frame

    @staticmethod
    def _open_m3u(path: str):
        """Открывает m3u-файл системным плеером."""
        try:
            os.startfile(path)
        except Exception:
            logger.exception("Не удалось открыть m3u-файл '%s'.", path)

    def play_track(self, track, api=None):
        """Открывает трек во внешнем плеере.

        Если передан api, создаёт временный m3u с прямой ссылкой.
        Иначе открывает страницу трека в браузере.
        """
        track_id = getattr(track, 'id', None)
        if not track_id:
            return
        if api is None:
            return

        title = self._track_title(track)
        url = api.get_track_direct_url(track)
        if not url:
            logger.warning("Не удалось получить ссылку для трека %s.", track_id)
            return

        fd, temp_path = tempfile.mkstemp(suffix=".m3u", prefix="yandex_track_")
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write("#EXTM3U\n")
            f.write(f"#EXTINF:-1,{title}\n")
            f.write(f"{url}\n")
        logger.info("Открытие трека %s во внешнем плеере.", track_id)
        self._open_m3u(temp_path)

    def play_album(self, album, api=None):
        """Открывает альбом во внешнем плеере через m3u.

        Загружает все треки альбома, получает прямые ссылки
        и записывает их во временный m3u-файл.
        """
        album_id = getattr(album, 'id', None)
        title = getattr(album, 'title', 'Альбом')
        if not album_id or api is None:
            return

        try:
            full = api.client.albums_with_tracks(album_id)
            if full is None or not full.volumes:
                logger.warning("Не удалось загрузить треки альбома %s.", album_id)
                return
            tracks = [t for vol in full.volumes for t in vol]
        except Exception:
            logger.exception("Ошибка загрузки альбома %s.", album_id)
            return

        self._write_and_open_m3u(tracks, api, f"yandex_album_{album_id}_", title)

    def play_playlist(self, playlist, api=None):
        """Открывает плейлист во внешнем плеере через m3u."""
        kind = getattr(playlist, 'kind', None)
        pl_title = getattr(playlist, 'title', 'Плейлист')
        if not kind or api is None:
            return

        try:
            shorts = api.get_playlist_tracks(playlist)
            tracks = []
            for short in shorts:
                track = getattr(short, 'track', None)
                tracks.append(track if track is not None else short)
        except Exception:
            logger.exception("Ошибка загрузки треков плейлиста %s.", kind)
            return

        self._write_and_open_m3u(tracks, api, f"yandex_playlist_{kind}_", pl_title)

    def _write_and_open_m3u(self, tracks, api, prefix, group_title):
        """Записывает треки во временный m3u и открывает его с прогрессом."""
        entries = []
        total = len(tracks)
        for i, track in enumerate(tracks):
            url = api.get_track_direct_url(track)
            if url:
                title = self._track_title(track)
                entries.append((title, url))
            if self.frame and hasattr(self.frame, 'dl_gauge'):
                pct = int(((i + 1) / max(1, total)) * 100)
                wx.CallAfter(self.frame.dl_gauge.SetValue, pct)
                wx.CallAfter(lambda: setattr(self.frame, '_last_download_percent', pct))

        if not entries:
            logger.warning("Нет треков с доступными ссылками для '%s'.", group_title)
            return

        fd, temp_path = tempfile.mkstemp(suffix=".m3u", prefix=prefix)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write("#EXTM3U\n")
            for title, url in entries:
                f.write(f"#EXTINF:-1,{title}\n")
                f.write(f"{url}\n")
        logger.info("Создан m3u '%s' (%d треков), открываю...", group_title, len(entries))
        self._open_m3u(temp_path)

    @staticmethod
    def _track_title(track) -> str:
        artists = ", ".join(a.name for a in (getattr(track, 'artists') or []))
        title = getattr(track, 'title', str(getattr(track, 'id', '')))
        return f"{artists} - {title}" if artists else title
