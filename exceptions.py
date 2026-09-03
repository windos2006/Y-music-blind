# Year: 2026
"""Модуль пользовательских исключений для y-music-blind."""

class YMusicBaseError(Exception):
    """Базовое исключение для всех ошибок приложения."""

class AuthError(YMusicBaseError):
    """Ошибка авторизации в Яндекс.Музыке."""

class NetworkError(YMusicBaseError):
    """Ошибка сетевого взаимодействия."""

class DownloadError(YMusicBaseError):
    """Ошибка при скачивании трека."""

class PlaybackError(YMusicBaseError):
    """Ошибка воспроизведения трека."""

class ConfigError(YMusicBaseError):
    """Ошибка загрузки или сохранения конфигурации."""

class AccountError(YMusicBaseError):
    """Ошибка управления учётными записями."""

class DeviceAuthCanceled(YMusicBaseError):
    """Авторизация устройства была отменена пользователем."""
