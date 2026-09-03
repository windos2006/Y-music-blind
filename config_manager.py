# Year: 2026
"""Управление конфигурацией приложения.

Хранит настройки в JSON-файле config.json: громкость, директория загрузок,
параметры речи, уровень логирования и т.д.
"""

import os
import json
import logging
from utils import get_data_dir

logger = logging.getLogger(__name__)

class ConfigManager:
    """Загрузка, сохранение и доступ к настройкам программы."""

    def __init__(self, config_path=None):
        self.config_path = config_path or os.path.join(get_data_dir(), 'config.json')
        self.config = self.get_default_config()
        self.load()

    def get_default_config(self) -> dict:
        """Возвращает словарь с настройками по умолчанию."""
        downloads_path = os.path.join(os.path.expanduser('~'), 'Downloads')
        if not os.path.exists(downloads_path):
            downloads_path = os.path.expanduser('~')

        return {
            "volume": 1.0,
            "download_dir": downloads_path,
            "detailed_errors": True,
            "show_download_dialog": True,
            "enable_speech": True,
            "enable_sapi5": False,
            "speech_media_state": True,
            "speech_general": True,
            "log_level": "INFO",
            "enable_logging": True,
            "clear_logs_on_startup": True,
            "remember_cursor": True,
            "show_track_album": True,
            "check_updates_on_startup": False,
            "minimize_to_tray": False,
            "tray_show_track_name": False,
            "dark_theme": False,
            "font_size": 10,
            "time_format": "short",
        }

    def load(self) -> None:
        """Загружает конфигурацию из config.json, если файл существует."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.config.update(data)
            except Exception:
                logging.basicConfig()
                logger.exception("Ошибка загрузки конфигурационного файла '%s'.", self.config_path)

    def save(self) -> None:
        """Сохраняет текущую конфигурацию в config.json."""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except Exception:
            logger.exception("Ошибка сохранения конфигурационного файла '%s'.", self.config_path)

    def get(self, key: str, default=None):
        """Возвращает значение настройки по ключу или default."""
        return self.config.get(key, default)

    def set(self, key: str, value) -> None:
        """Устанавливает значение настройки и сохраняет файл."""
        self.config[key] = value
        self.save()
