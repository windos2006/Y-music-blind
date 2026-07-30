# Year: 2026
import os
import json
import logging
import requests
from typing import Optional
from yandex_music import Client
from exceptions import AuthError, NetworkError

logger = logging.getLogger(__name__)

class YandexMusicManager:
    def __init__(self, token_filepath: str = "auth_data.json"):
        self.token_filepath = token_filepath
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

    def _find_genres_recursive(self, genres_list, query):
        found = []
        for g in genres_list:
            if query.lower() in g.title.lower():
                found.append(g)
            if hasattr(g, 'sub_genres') and g.sub_genres:
                found.extend(self._find_genres_recursive(g.sub_genres, query))
        return found

    def search(self, query: str, search_type: str = "track", page: int = 0):
        if not self.client:
            return [], False
        try:
            if search_type == "genre":
                all_genres = self.client.genres()
                matched = self._find_genres_recursive(all_genres, query)
                return matched, False

            res = self.client.search(text=query, page=page, type_=search_type)
            if not res:
                return [], False

            if search_type == "track" and res.tracks:
                has_next = (res.tracks.total > (page + 1) * 20) if res.tracks.total else False
                return res.tracks.results, has_next
            elif search_type == "artist" and res.artists:
                has_next = (res.artists.total > (page + 1) * 20) if res.artists.total else False
                return res.artists.results, has_next

            return [], False
        except Exception:
            logger.exception("Ошибка при поиске в Яндекс Музыке.")
            return [], False

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
            albums = self.client.artists_direct_albums(artist_id, page=page, page_size=100)
            if albums:
                return albums, len(albums) == 100
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

    def get_genre_tracks_batch(
        self,
        genre_id: str,
        min_count: int = 20,
        existing_seen_ids: set = None,
    ):
        """
        Загружает треки жанра одним раундом параллельных запросов.
        Каждый запрос — независимая сессия ротора, что даёт случайную выборку
        из доступного пула треков жанра.

        12 воркеров, таймаут 10 с.
        Первая загрузка: ~12-18 треков, дозагрузка: остатки пула.

        Возвращает (tracks, has_next=True) — радиостанция бесконечна.
        """
        if not self.client:
            return [], False

        import concurrent.futures

        station_id = f"genre:{genre_id}"
        new_tracks = []
        seen_ids = set(existing_seen_ids) if existing_seen_ids else set()

        workers = 12
        request_timeout = 10

        try:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=workers,
            ) as pool:
                futures = [
                    pool.submit(
                        self.client.rotor_station_tracks,
                        station_id,
                        None,
                    )
                    for _ in range(workers)
                ]
                for future in concurrent.futures.as_completed(futures):
                    try:
                        batch = future.result(timeout=request_timeout)
                    except Exception as exc:
                        logger.debug(
                            "Запрос ротора (жанр %s) упал: %s",
                            genre_id, exc,
                        )
                        continue
                    if not batch or not batch.sequence:
                        continue
                    for item in batch.sequence:
                        track = getattr(item, 'track', None)
                        if not track:
                            continue
                        tid = str(track.id)
                        if tid not in seen_ids:
                            seen_ids.add(tid)
                            new_tracks.append(track)

            if new_tracks:
                logger.info(
                    "Жанр %s: загружено %d треков (%d воркеров).",
                    genre_id, len(new_tracks), workers,
                )
                return new_tracks[:min_count], True

            logger.warning("Не удалось получить новые треки для жанра %s.", genre_id)
            return [], True

        except Exception:
            logger.exception("Ошибка загрузки треков жанра %s.", genre_id)
            return [], True

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
