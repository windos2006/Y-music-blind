# Year: 2026
import logging
import sys
import os
import platform
import struct
from utils import get_data_dir

def log_system_info():
    """Логирует информацию о системе при запуске: версия, ОС, архитектура, Python, библиотеки."""
    logger = logging.getLogger(__name__)
    from version import VERSION

    logger.info("y-music-blind v%s", VERSION)

    os_name = platform.system()
    os_release = platform.release()
    os_version = platform.version()

    # Корректное определение разрядности ОС, даже если Python 32-битный
    # PROCESSOR_ARCHITEW6432 выставляется только в 32-битном процессе на 64-битной ОС
    if 'PROCESSOR_ARCHITEW6432' in os.environ:
        os_arch = '64-bit'
    elif os.environ.get('PROCESSOR_ARCHITECTURE', '').endswith('64'):
        os_arch = '64-bit'
    elif platform.machine().endswith('64'):
        os_arch = '64-bit'
    else:
        os_arch = '32-bit'

    py_arch = struct.calcsize("P") * 8
    py_version = sys.version.replace('\n', ' ')

    libs = {}
    try:
        import yandex_music
        libs['yandex_music'] = getattr(yandex_music, '__version__', '?')
    except ImportError:
        pass
    try:
        import wx
        libs['wxPython'] = wx.__version__
    except ImportError:
        pass
    try:
        import requests
        libs['requests'] = requests.__version__
    except ImportError:
        pass

    logger.info("━" * 50)
    logger.info("Система: %s %s (%s)", os_name, os_release, os_arch)
    logger.info("Версия ОС: %s", os_version)
    logger.info("Python: %s (%d-bit)", py_version, py_arch)
    if libs:
        lib_ver = ", ".join(f"{k} {v}" for k, v in libs.items())
        logger.info("Библиотеки: %s", lib_ver)
    logger.info("━" * 50)


def setup_logger(config):
    enable_logging = config.get("enable_logging", True)
    log_level_str = config.get("log_level", "INFO")
    level = getattr(logging, log_level_str.upper(), logging.INFO)
    clear_logs = config.get("clear_logs_on_startup", True)

    logger = logging.getLogger()

    # Убираем старые обработчики, чтобы переинициализация не дублировала записи
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    if not enable_logging:
        logger.setLevel(logging.CRITICAL + 1)
        return

    logger.setLevel(level)

    file_mode = 'w' if clear_logs else 'a'

    # Формат: Уровень - Дата - Модуль - Сообщение
    formatter = logging.Formatter('%(levelname)s - %(asctime)s - %(module)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

    # Файловый логгер
    log_path = os.path.join(get_data_dir(), 'wm_music_blind.log')
    fh = logging.FileHandler(log_path, mode=file_mode, encoding='utf-8')
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    # Логгер в консоль (для отладки)
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # Исправление для yandex_music: явно указываем обработчики для пространства имен библиотеки
    ym_logger = logging.getLogger("yandex_music")
    ym_logger.setLevel(level)
    ym_logger.addHandler(fh)
    ym_logger.addHandler(ch)

    # Глобальный перехват необработанных исключений
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.error("Необработанное исключение", exc_info=(exc_type, exc_value, exc_traceback))

    sys.excepthook = handle_exception


def log_exception(context: str, exc: Exception = None, exc_info=None) -> None:
    """Логирует исключение с полной трассировкой стека.

    Используется для обработчиков ошибок, чтобы даже «обработанные»
    исключения попадали в журнал приложения с контекстом.
    """
    logger = logging.getLogger(__name__)
    if exc_info is None and exc is not None:
        exc_info = (type(exc), exc, exc.__traceback__)
    logger.error("Ошибка: %s", context, exc_info=exc_info)