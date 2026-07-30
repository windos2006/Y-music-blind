# Year: 2026
"""Управление конфигурацией приложения.

Хранит настройки в JSON-файле config.json: громкость, директория загрузок,
параметры речи, уровень логирования и т.д.
"""

import os
import json
import logging

logger = logging.getLogger(__name__)

CONFIG_FILE = 'config.json'

class ConfigManager:
    """Загрузка, сохранение и доступ к настройкам программы."""

    def __init__(self):
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
            "enable_speech": False,
            "enable_sapi5": False,
            "speech_media_state": True,
            "speech_general": True,
            "log_level": "INFO",
            "clear_logs_on_startup": True,
            "remember_cursor": True,
        }

    def load(self) -> None:
        """Загружает конфигурацию из config.json, если файл существует."""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.config.update(data)
            except Exception:
                logging.basicConfig()
                logger.exception("Ошибка загрузки конфигурационного файла '%s'.", CONFIG_FILE)

    def save(self) -> None:
        """Сохраняет текущую конфигурацию в config.json."""
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except Exception:
            logger.exception("Ошибка сохранения конфигурационного файла '%s'.", CONFIG_FILE)

    def get(self, key: str, default=None):
        """Возвращает значение настройки по ключу или default."""
        return self.config.get(key, default)

    def set(self, key: str, value) -> None:
        """Устанавливает значение настройки и сохраняет файл."""
        self.config[key] = value
        self.save()
