# Year: 2026
import os
import zipfile
import threading
import logging
import wx

logger = logging.getLogger(__name__)


def _safe_filename(title, fallback="track"):
    """Возвращает безопасное имя файла из названия трека."""
    safe = "".join(c for c in title if c.isalnum() or c in " -_").strip()
    return safe or fallback


def _track_artist(track):
    """Возвращает строку с именами исполнителей трека."""
    try:
        artists = getattr(track, 'artists', None) or []
        return ", ".join(a.name for a in artists)
    except Exception:
        return ""


def _progress(frame, current, total):
    """Обновляет индикатор прогресса скачивания ZIP-архива."""
    def _update():
        if total > 0:
            percent = int((current / total) * 100)
            percent = min(100, max(0, percent))
            frame.dl_gauge.SetValue(percent)
            frame._last_download_percent = percent
    wx.CallAfter(_update)


def _zip_tracks(frame, api, tracks, archive_path, zip_name):
    """Пишет треки в ZIP-архив с обновлением прогресса.

    Использует официальный метод download_bytes() из SDK yandex-music.
    """
    total = len(tracks)
    done = 0
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, track in enumerate(tracks):
            try:
                data = None
                if hasattr(track, 'download_bytes'):
                    data = track.download_bytes()
                elif hasattr(track, 'downloadBytes'):
                    data = track.downloadBytes()
                elif hasattr(api, 'client') and api.client:
                    # Попытка через download_info или прямую ссылку
                    url = api.get_track_direct_url(track)
                    if url:
                        import requests
                        resp = requests.get(url, timeout=30)
                        if resp.status_code == 200:
                            data = resp.content

                if data:
                    artist = _track_artist(track)
                    filename = _safe_filename(f"{artist} - {track.title}" if artist else getattr(track, 'title', 'track'))
                    arcname = f"{zip_name}/{i+1:02d} - {filename}.mp3"
                    zf.writestr(arcname, data)
            except Exception:
                logger.exception("Ошибка при скачивании трека %s в архив.", getattr(track, 'id', track))
            done += 1
            _progress(frame, done, total)


def download_items_to_zip(frame, api, items):
    """Скачивает список треков (или других объектов) в ZIP-архив.

    Из объектов извлекаются только треки. Архив сохраняется в папку
    загрузок с именем «список.zip».
    """
    tracks = []
    for item in items:
        if type(item).__name__ == 'Track' or (hasattr(item, 'artists') and hasattr(item, 'title')):
            tracks.append(item)
    if not tracks:
        wx.CallAfter(frame.show_error, "В списке нет треков для скачивания.")
        return

    download_dir = frame.config.get("download_dir", "")
    archive_path = os.path.join(download_dir, "список.zip")
    frame.dl_gauge.SetValue(0)
    wx.CallAfter(frame.dl_gauge.Show)
    wx.CallAfter(frame.safe_speak, "Скачивание списка в ZIP-архив", "general")

    try:
        _zip_tracks(frame, api, tracks, archive_path, "список")
        wx.CallAfter(_finish_zip, frame, archive_path)
    except Exception as e:
        wx.CallAfter(frame.show_error, "Ошибка при создании ZIP-архива", e)
    finally:
        wx.CallAfter(frame.dl_gauge.Hide)
        wx.CallAfter(frame.panel.Layout)


def download_playlist_to_zip(frame, api, playlist):
    """Скачивает все треки плейлиста в ZIP-архив."""
    try:
        shorts = api.get_playlist_tracks(playlist)
        tracks = []
        for short in shorts:
            track = getattr(short, 'track', None)
            tracks.append(track if track is not None else short)
    except Exception as e:
        wx.CallAfter(frame.show_error, "Не удалось загрузить треки плейлиста", e)
        return

    if not tracks:
        wx.CallAfter(frame.show_error, "В плейлисте нет треков.")
        return

    download_dir = frame.config.get("download_dir", "")
    safe_title = _safe_filename(getattr(playlist, 'title', 'плейлист'), "плейлист")
    archive_path = os.path.join(download_dir, f"{safe_title}.zip")
    frame.dl_gauge.SetValue(0)
    wx.CallAfter(frame.dl_gauge.Show)
    wx.CallAfter(frame.safe_speak, f"Скачивание плейлиста в ZIP: {safe_title}", "general")

    try:
        _zip_tracks(frame, api, tracks, archive_path, safe_title)
        wx.CallAfter(_finish_zip, frame, archive_path)
    except Exception as e:
        wx.CallAfter(frame.show_error, "Ошибка при создании ZIP-архива", e)
    finally:
        wx.CallAfter(frame.dl_gauge.Hide)
        wx.CallAfter(frame.panel.Layout)


def download_album_to_zip(frame, api, album):
    """Скачивает альбом в ZIP-архив (треки из volumes)."""
    try:
        full_album = api.client.albums_with_tracks(album.id)
        tracks = []
        if hasattr(full_album, 'volumes') and full_album.volumes:
            for volume in full_album.volumes:
                tracks.extend(volume)
        elif hasattr(full_album, 'tracks') and full_album.tracks:
            tracks = full_album.tracks
    except Exception as e:
        wx.CallAfter(frame.show_error, "Не удалось загрузить треки альбома", e)
        return

    if not tracks:
        wx.CallAfter(frame.show_error, "В альбоме нет треков.")
        return

    download_dir = frame.config.get("download_dir", "")
    safe_title = _safe_filename(getattr(album, 'title', 'альбом'), "альбом")
    archive_path = os.path.join(download_dir, f"{safe_title}.zip")
    frame.dl_gauge.SetValue(0)
    wx.CallAfter(frame.dl_gauge.Show)
    wx.CallAfter(frame.safe_speak, f"Скачивание альбома в ZIP: {safe_title}", "general")

    try:
        _zip_tracks(frame, api, tracks, archive_path, safe_title)
        wx.CallAfter(_finish_zip, frame, archive_path)
    except Exception as e:
        wx.CallAfter(frame.show_error, "Ошибка при создании ZIP-архива", e)
    finally:
        wx.CallAfter(frame.dl_gauge.Hide)
        wx.CallAfter(frame.panel.Layout)


def _finish_zip(frame, archive_path):
    """Сообщает о завершении скачивания архива и при необходимости показывает диалог."""
    frame.safe_speak("Архив успешно создан", "general")
    frame._last_download_percent = 100
    if frame.config.get("show_download_dialog", True):
        dlg = wx.MessageDialog(
            frame, 
            f"Архив успешно сохранён по пути:\n{archive_path}", 
            "Загрузка архива завершена", 
            wx.OK | wx.CANCEL | wx.ICON_INFORMATION
        )
        dlg.SetOKCancelLabels("ОК", "Показать в папке")
        if dlg.ShowModal() == wx.ID_CANCEL: 
            import subprocess
            subprocess.Popen(rf'explorer /select,"{archive_path.replace("/", "\\")}"')
        dlg.Destroy()
    else:
        from utils import play_ui_sound
        play_ui_sound('complete_download.wav')
