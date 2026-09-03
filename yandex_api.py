# Year: 2026
import os
import json
import logging
import requests
from typing import Optional
from yandex_music import Client
from exceptions import AuthError, NetworkError
from utils import get_data_dir

logger = logging.getLogger(__name__)

class YandexMusicManager:
    def __init__(self, token_filepath: str = None):
        self.token_filepath = token_filepath or os.path.join(get_data_dir(), "auth_data.json")
        self.client: Optional[Client] = None

    def save_token(self, token: str) -> None:
        try:
            with open(self.token_filepath, "w", encoding="utf-8") as f:
                json.dump({"token": token}, f, ensure_ascii=False, indent=4)
            logger.debug("Токен сохранен в файл.")
        except Exception:
            logger.exception("Ошибка сохранения токена в файл.")

    def clear_token(self) -> None:
        try:
            if os.path.exists(self.token_filepath):
                os.remove(self.token_filepath)
                logger.debug("Файл токена '%s' удалён.", self.token_filepath)
        except Exception:
            logger.exception("Ошибка удаления файла токена '%s'.", self.token_filepath)

    def load_token(self) -> Optional[str]:
        if not os.path.exists(self.token_filepath):
            return None
        try:
            with open(self.token_filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("token")
        except Exception:
            logger.exception("Ошибка чтения токена из файла.")
            return None

    def auth(self, token: str):
        try:
            self.client = Client(token).init()
            self.save_token(token)
            logger.info("Успешная авторизация в Яндекс Музыке.")
            return True, "OK", "Успешная авторизация"
        except Exception as e:
            err_msg = str(e)
            status = "NETWORK" if "Network" in err_msg or "Connection" in err_msg else "ERROR"
            logger.exception("Ошибка инициализации клиента Yandex Music.")
            return False, status, err_msg

    def search(self, query: str, search_type: str = "track", page: int = 0):
        if not self.client:
            return [], False
        try:
            if search_type == "track":
                all_tracks = []
                has_next = False
                start_api_page = page * 5
                for p in range(start_api_page, start_api_page + 5):
                    res = self.client.search(text=query, page=p, type_="track")
                    if not res or not res.tracks or not res.tracks.results:
                        break
                    all_tracks.extend(res.tracks.results)
                    if res.tracks.total and res.tracks.total > (p + 1) * 20:
                        has_next = True
                    else:
                        has_next = False
                        break
                return all_tracks, has_next
            elif search_type == "artist":
                res = self.client.search(text=query, page=page, type_="artist")
                if not res or not res.artists:
                    return [], False
                has_next = (res.artists.total > (page + 1) * 20) if res.artists.total else False
                return res.artists.results, has_next
            elif search_type == "album":
                res = self.client.search(text=query, page=page, type_="album")
                if not res or not res.albums:
                    return [], False
                has_next = (res.albums.total > (page + 1) * 20) if res.albums.total else False
                return res.albums.results, has_next

            return [], False
        except Exception:
            logger.exception("Ошибка при поиске в Яндекс Музыке.")
            raise

    def get_artist_tracks(self, artist_id, page=0):
        if not self.client:
            return [], False
        try:
            tracks = self.client.artists_tracks(artist_id, page=page, page_size=100)
            if tracks and tracks.tracks:
                return tracks.tracks, len(tracks.tracks) == 100
            return [], False
        except Exception:
            logger.exception("Ошибка при загрузке треков исполнителя.")
            return [], False

    def get_artist_albums(self, artist_id, page=0):
        if not self.client:
            return [], False
        try:
            res = self.client.artists_direct_albums(artist_id, page=page, page_size=100)
            if res and res.albums:
                return res.albums, len(res.albums) == 100
            return [], False
        except Exception:
            logger.exception("Ошибка при загрузке альбомов исполнителя.")
            return [], False

    def get_track_direct_url(self, track):
        """Получает прямую MP3 ссылку высшего доступного качества."""
        try:
            download_info = track.get_download_info(get_direct_links=True)
            if download_info:
                download_info.sort(key=lambda x: x.bitrate_in_kbps, reverse=True)
                return download_info[0].direct_link
        except Exception:
            logger.exception("Ошибка получения прямой ссылки на трек.")
        return None

    def download_track(self, track, save_path, progress_callback=None):
        """Скачивает трек в локальный файл."""
        try:
            url = self.get_track_direct_url(track)
            if not url:
                return False, "Не удалось получить ссылку для скачивания."

            response = requests.get(url, stream=True)
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))

            with open(save_path, 'wb') as f:
                downloaded = 0
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            progress_callback(downloaded, total_size)
            return True, ""
        except Exception as e:
            logger.exception("Ошибка скачивания файла.")
            return False, str(e)

    def like_track(self, track_id):
        if not self.client: return
        try:
            self.client.users_likes_tracks_add(track_id)
        except Exception:
            logger.exception("Ошибка установки лайка треку.")

    def dislike_track(self, track_id):
        if not self.client: return
        try:
            self.client.users_likes_tracks_remove(track_id)
        except Exception:
            logger.exception("Ошибка снятия лайка треку.")

    def like_album(self, album_id):
        if not self.client: return
        try:
            self.client.users_likes_albums_add(album_id)
        except Exception:
            logger.exception("Ошибка установки лайка альбому.")

    def dislike_album(self, album_id):
        if not self.client: return
        try:
            self.client.users_likes_albums_remove(album_id)
        except Exception:
            logger.exception("Ошибка снятия лайка альбому.")

    # ── Универсальный классификатор объектов ─────────────────────────────────

    def classify_item(self, obj):
        """Определяет тип объекта: track, album, playlist, artist или other.

        Используется в главном окне для построения контекстных меню и
        обработки горячих клавиш.
        """
        type_name = type(obj).__name__

        if type_name == 'Playlist':
            return 'playlist'
        if type_name == 'Album':
            return 'album'
        if type_name == 'Track':
            return 'track'
        if type_name == 'Artist':
            return 'artist'

        has_title = hasattr(obj, 'title')
        has_track_count = hasattr(obj, 'track_count')
        has_artists = hasattr(obj, 'artists')
        has_name = hasattr(obj, 'name')

        if has_track_count and has_title and hasattr(obj, 'uid'):
            return 'playlist'
        if has_track_count and has_title:
            return 'album'
        if has_artists and has_title:
            return 'track'
        if has_name and not has_title:
            return 'artist'
        return 'other'

    # ── Подкасты и выпуски ────────────────────────────────────────────────────

    def search_podcasts(self, query: str, page: int = 0):
        """Ищет подкасты по запросу. Возвращает (список альбомов, есть ли дальше)."""
        if not self.client:
            return [], False
        try:
            res = self.client.search(text=query, page=page, type_="podcast")
            if not res or not res.podcasts:
                return [], False
            has_next = (res.podcasts.total > (page + 1) * 20) if res.podcasts.total else False
            return res.podcasts.results, has_next
        except Exception:
            logger.exception("Ошибка при поиске подкастов.")
            return [], False

    def search_podcast_episodes(self, query: str, page: int = 0):
        """Ищет выпуски подкастов по запросу. Возвращает (список треков, есть ли дальше)."""
        if not self.client:
            return [], False
        try:
            res = self.client.search(text=query, page=page, type_="podcast_episode")
            if not res or not res.podcast_episodes:
                return [], False
            has_next = (res.podcast_episodes.total > (page + 1) * 20) if res.podcast_episodes.total else False
            return res.podcast_episodes.results, has_next
        except Exception:
            logger.exception("Ошибка при поиске выпусков подкастов.")
            return [], False

    def get_podcast_episodes(self, podcast_id: str, page: int = 0):
        """Возвращает список выпусков подкаста. Возвращает (треки, есть ли дальше)."""
        if not self.client:
            return [], False
        try:
            album = self.client.albums_with_tracks(podcast_id)
            if album is None or not album.volumes:
                return [], False
            tracks = [t for vol in album.volumes for t in vol]
            return tracks, False
        except Exception:
            logger.exception("Ошибка при загрузке выпусков подкаста.")
            return [], False

    # ── Плейлисты ─────────────────────────────────────────────────────────────

    def get_users_playlists(self):
        """Возвращает список плейлистов текущего пользователя."""
        if not self.client:
            return []
        try:
            return self.client.users_playlists_list()
        except Exception:
            logger.exception("Ошибка при загрузке плейлистов пользователя.")
            return []

    def get_playlist_tracks(self, playlist):
        """Возвращает список треков плейлиста.

        Плейлист из поиска может не содержать полных данных, поэтому
        сначала он догружается через users_playlists, затем из него
        извлекаются треки (TrackShort, содержащие сами Track-объекты).
        """
        if not self.client:
            return []
        try:
            kind = getattr(playlist, 'kind', None)
            if kind is None:
                return []
            owner = getattr(playlist, 'owner', None)
            uid = getattr(owner, 'uid', None) if owner else None
            if uid is None:
                uid = getattr(playlist, 'uid', None)
            try:
                kind_int = int(kind)
            except (TypeError, ValueError):
                kind_int = kind
            full = self.client.users_playlists(kind_int, uid)
            if full is None:
                return []
            tracks = getattr(full, 'tracks', None)
            if not tracks:
                return []
            return list(tracks)
        except Exception:
            logger.exception("Ошибка при загрузке треков плейлиста.")
            return []

    def create_playlist(self, title: str):
        """Создаёт новый плейлист с заданным названием."""
        if not self.client:
            return None
        try:
            return self.client.users_playlists_create(title=title)
        except Exception:
            logger.exception("Ошибка при создании плейлиста.")
            return None

    def delete_playlist(self, kind: str):
        """Удаляет плейлист по его номеру (kind)."""
        if not self.client:
            return False
        try:
            self.client.users_playlists_delete(kind)
            return True
        except Exception:
            logger.exception("Ошибка при удалении плейлиста.")
            return False

    def rename_playlist(self, kind: str, new_title: str):
        """Переименовывает плейлист по его номеру."""
        if not self.client:
            return False
        try:
            self.client.users_playlists_name(kind, new_title)
            return True
        except Exception:
            logger.exception("Ошибка при переименовании плейлиста.")
            return False

    def add_tracks_to_playlist(self, kind: str, track_pairs, revision: int = None):
        """Добавляет треки в конец плейлиста.

        Параметры: kind — номер плейлиста, track_pairs — список пар
        (album_id, track_id). Если revision не передан, он берётся
        из текущего состояния плейлиста (обязательно для API).
        Возвращает True при успехе.
        """
        if not self.client:
            return False
        try:
            user_id = self.client.account_uid
            kind_int = int(kind) if isinstance(kind, str) else kind

            if revision is None:
                pl = self.client.users_playlists(kind_int, user_id)
                if pl is None:
                    logger.warning("Не удалось получить плейлист %s для определения revision.", kind)
                    return False
                revision = getattr(pl, 'revision', 1)

            at = getattr(pl, 'track_count', 0) if pl is not None else 0

            ok = True
            for album_id, track_id in track_pairs:
                diff = json.dumps([{
                    "op": "insert",
                    "at": at,
                    "tracks": [{"id": int(track_id), "albumId": int(album_id)}],
                }], ensure_ascii=False)
                result = self.client.users_playlists_change(
                    kind_int, diff, revision=revision, user_id=user_id
                )
                if result is None:
                    ok = False
                    logger.warning("Не удалось добавить трек %s в плейлист %s.", track_id, kind)
                else:
                    revision = getattr(result, 'revision', revision + 1)
                    at += 1
            return ok
        except Exception:
            logger.exception("Ошибка при добавлении треков в плейлист.")
            return False

    def remove_track_from_playlist(self, kind: str, index: int, revision: int = 1):
        """Удаляет трек из плейлиста по его индексу (с 0)."""
        if not self.client:
            return False
        try:
            result = self.client.users_playlists_delete_track(
                kind, index, index, revision=revision
            )
            return result is not None
        except Exception:
            logger.exception("Ошибка при удалении трека из плейлиста.")
            return False

    def like_playlist(self, playlist):
        """Ставит лайк плейлисту. Идентификатор — в формате owner_id:kind."""
        if not self.client:
            return
        try:
            uid = self._owner_id(playlist)
            self.client.users_likes_playlists_add(f"{uid}:{playlist.kind}")
        except Exception:
            logger.exception("Ошибка установки лайка плейлисту.")

    def dislike_playlist(self, playlist):
        """Снимает лайк с плейлиста. Идентификатор — в формате owner_id:kind."""
        if not self.client:
            return
        try:
            uid = self._owner_id(playlist)
            self.client.users_likes_playlists_remove(f"{uid}:{playlist.kind}")
        except Exception:
            logger.exception("Ошибка снятия лайка с плейлиста.")

    # ── Плейлисты: методы из документации ────────────────────────────────────

    def get_playlist_recommendations(self, kind, user_id=None):
        """Возвращает рекомендации похожих плейлистов для плейлиста kind.

        user_id — идентификатор пользователя, для которого строятся
        рекомендации; по умолчанию берётся текущий.
        """
        if not self.client:
            return None
        try:
            return self.client.users_playlists_recommendations(kind, user_id)
        except Exception:
            logger.exception("Ошибка при загрузке рекомендаций плейлиста.")
            return None

    def set_playlist_visibility(self, kind, visibility: str, user_id=None):
        """Задаёт видимость плейлиста: 'public' или 'private'."""
        if not self.client:
            return None
        try:
            return self.client.users_playlists_visibility(kind, visibility, user_id)
        except Exception:
            logger.exception("Ошибка при изменении видимости плейлиста.")
            return None

    def set_playlist_description(self, kind, description: str, user_id=None):
        """Задаёт описание плейлиста."""
        if not self.client:
            return None
        try:
            return self.client.users_playlists_description(kind, description, user_id)
        except Exception:
            logger.exception("Ошибка при изменении описания плейлиста.")
            return None

    def change_playlist(self, kind, diff, revision: int = 1, user_id=None):
        """Применяет изменения к плейлисту через JSON-разность.

        diff может быть строкой (JSON) или словарём — в этом случае он
        будет преобразован в JSON автоматически. Возвращает обновлённый
        плейлист или None при ошибке.
        """
        if not self.client:
            return None
        try:
            if isinstance(diff, dict):
                diff = json.dumps(diff, ensure_ascii=False)
            return self.client.users_playlists_change(kind, diff, revision=revision, user_id=user_id)
        except Exception:
            logger.exception("Ошибка при изменении плейлиста.")
            return None

    def get_playlist_by_uuid(self, playlist_uuid: str):
        """Возвращает плейлист по его UUID."""
        if not self.client:
            return None
        try:
            return self.client.playlist(playlist_uuid)
        except Exception:
            logger.exception("Ошибка при загрузке плейлиста по UUID.")
            return None

    def get_playlist_similar_entities(self, playlist_uuid: str):
        """Возвращает похожие сущности (треки, плейлисты) для плейлиста."""
        if not self.client:
            return None
        try:
            return self.client.playlist_similar_entities(playlist_uuid)
        except Exception:
            logger.exception("Ошибка при загрузке похожих сущностей плейлиста.")
            return None

    def get_playlists_by_ids(self, playlist_ids):
        """Возвращает подробные данные плейлистов по списку идентификаторов.

        Идентификаторы вида 'owner_id:kind' или числовые.
        """
        if not self.client:
            return None
        try:
            return self.client.playlists(playlist_ids)
        except Exception:
            logger.exception("Ошибка при загрузке плейлистов по идентификаторам.")
            return None

    def get_playlists_short(self, playlist_ids):
        """Возвращает краткие данные плейлистов по списку идентификаторов."""
        if not self.client:
            return None
        try:
            return self.client.playlists_list(playlist_ids)
        except Exception:
            logger.exception("Ошибка при загрузке кратких данных плейлистов.")
            return None

    def get_personal_playlist(self, playlist_id: str):
        """Возвращает персональный плейлист пользователя.

        Известные значения playlist_id: daily (Плейлист дня), missedLikes
        (Тайник), recentTracks (Премьера), neverHeard (Дежавю), podcasts
        (Подкасты недели), origin (Плейлист с Алисой).
        """
        if not self.client:
            return None
        try:
            return self.client.playlists_personal(playlist_id)
        except Exception:
            logger.exception("Ошибка при загрузке персонального плейлиста.")
            return None

    def get_user_settings(self, user_id=None):
        """Возвращает настройки пользователя."""
        if not self.client:
            return None
        try:
            return self.client.users_settings(user_id)
        except Exception:
            logger.exception("Ошибка при загрузке настроек пользователя.")
            return None

    def get_user_playlist_kinds(self, user_id=None):
        """Возвращает список номеров (kind) плейлистов пользователя."""
        if not self.client:
            return []
        try:
            return self.client.users_playlists_kinds(user_id) or []
        except Exception:
            logger.exception("Ошибка при загрузке номеров плейлистов пользователя.")
            return []

    def get_playlist_trailer(self, kind, user_id=None):
        """Возвращает трейлер плейлиста (видео-тизер)."""
        if not self.client:
            return None
        try:
            return self.client.users_playlists_trailer(kind, user_id)
        except Exception:
            logger.exception("Ошибка при загрузке трейлера плейлиста.")
            return None

    @staticmethod
    def _owner_id(playlist):
        """Возвращает числовой идентификатор владельца плейлиста."""
        owner = getattr(playlist, 'owner', None)
        if owner is not None:
            uid = getattr(owner, 'uid', None)
            if uid:
                return uid
            login = getattr(owner, 'login', None)
            if login:
                return login
        uid = getattr(playlist, 'uid', None)
        return uid or 0

    # ── Умная пагинация ───────────────────────────────────────────────────────

    def load_more_album_tracks(self, album, existing: list, page: int = 0):
        """Подгружает следующую часть треков альбома (volumes)."""
        if not self.client:
            return existing, False
        try:
            full = self.client.albums_with_tracks(album.id)
            if full is None or not full.volumes:
                return existing, False
            all_tracks = [t for vol in full.volumes for t in vol]
            if page + 1 < len(full.volumes):
                return all_tracks, True
            return all_tracks, False
        except Exception:
            logger.exception("Ошибка при дозагрузке треков альбома.")
            return existing, False

    # ── Асинхронная прямая ссылка ─────────────────────────────────────────────

    def get_track_direct_url_async(self, track, callback):
        """Запрашивает прямую ссылку в отдельном потоке и вызывает callback.

        Используется для скачивания трека без блокировки интерфейса.
        """
        import threading
        def worker():
            url = self.get_track_direct_url(track)
            try:
                callback(url)
            except Exception:
                logger.exception("Ошибка в колбэке асинхронного получения ссылки.")
        threading.Thread(target=worker, daemon=True).start()
