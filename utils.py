# Year: 2026
"""Вспомогательные утилиты для работы с путями и ресурсами.

Учитывает особенности упаковки PyInstaller: в скомпилированном EXE
пути к ресурсам берутся из sys._MEIPASS (временная папка распаковки).

Пользовательские данные (config.json, accounts.json, auth_data.json,
логи) хранятся в папке data рядом с приложением — это сохраняет
портабельность программы.
если в папке с приложэнием небудет папки data то данные будут хронится в папке abv-data тикущива пользователя
"""
import sys
import os

def get_resource_path(relative_path):
    """Возвращает абсолютный путь к ресурсу, учитывая распаковку PyInstaller."""
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

def get_app_dir():
    """Возвращает директорию, где лежит приложение (exe или исходники)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

import sys
import os
import tempfile

def get_resource_path(relative_path):
    """Возвращает абсолютный путь к ресурсу, учитывая распаковку PyInstaller."""
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

def get_app_dir():
    """Возвращает директорию, где лежит приложение (exe или исходники)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def get_data_dir():
    """Возвращает папку данных приложения.

    Если папка data найдена рядом с программой — используем её.
    Если папка data отсутствует рядом с программой — сохраняем все данные
    в папку AppData текущего пользователя.
    """
    app_dir = get_app_dir()
    local_data_dir = os.path.join(app_dir, "data")
    if os.path.exists(local_data_dir) and os.path.isdir(local_data_dir):
        return local_data_dir
    else:
        appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
        data_dir = os.path.join(appdata, "y-music-blind")
        try:
            os.makedirs(data_dir, exist_ok=True)
        except OSError:
            pass
        return data_dir

def cleanup_temp_updater():
    """Удаляет временный файл updater.exe из временного каталога при запуске."""
    try:
        temp_dir = tempfile.gettempdir()
        for filename in os.listdir(temp_dir):
            if "updater" in filename.lower() and filename.lower().endswith(".exe"):
                path = os.path.join(temp_dir, filename)
                try:
                    os.remove(path)
                except Exception:
                    pass
    except Exception:
        pass

_LEGACY_DATA_FILES = (
    "config.json",
    "accounts.json",
    "auth_data.json",
    "wm_music_blind.log",
)

def classify_item(obj):
    """Определяет тип объекта Яндекс.Музыки.

    Возвращает одну из строк: 'track', 'album', 'playlist', 'artist' или 'other'.
    Подкасты представлены объектами Album, а их выпуски — объектами Track,
    поэтому отдельные типы для них не вводятся.

    Используется для единообразной работы со списком в главном окне:
    контекстные меню, горячие клавиши и отображение текста строк.
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

    # Запасные эвристики для объектов, у которых имя класса отличается
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

def migrate_legacy_data():
    """Переносит пользовательские файлы из старого расположения в папку data."""
    import shutil
    app_dir = get_app_dir()
    data_dir = get_data_dir()
    if os.path.normcase(app_dir) == os.path.normcase(data_dir):
        return
    for name in _LEGACY_DATA_FILES:
        legacy = os.path.join(app_dir, name)
        dest = os.path.join(data_dir, name)
        if os.path.isfile(legacy) and not os.path.exists(dest):
            try:
                shutil.copy2(legacy, dest)
            except OSError:
                pass