# Year: 2026
import ctypes
import os
import threading
import pythoncom
import win32com.client
import logging
import platform
import time
from utils import get_resource_path

logger = logging.getLogger(__name__)

class SpeechManager:
    """Управление речевым выводом через NVDA и SAPI5.

    Пытается использовать NVDA Controller Client для озвучивания,
    при недоступности — падает на SAPI5 (если разрешено в конфиге).
    """

    def __init__(self):
        self.nvda = None
        self.is_64bit = platform.architecture()[0] == '64bit'
        self.load_screen_readers()

    def load_screen_readers(self):
        """Загружает NVDA Controller DLL из папки speech/."""
        speech_dir = get_resource_path('speech')
        if not os.path.exists(speech_dir):
            logger.warning("Папка 'speech' не найдена. DLL файлы не загружены.")
            return

        nvda_name = 'nvdaControllerClient64.dll' if self.is_64bit else 'nvdaControllerClient32.dll'
        nvda_path = os.path.join(speech_dir, nvda_name)

        if os.path.exists(nvda_path):
            try:
                self.nvda = ctypes.windll.LoadLibrary(nvda_path)
                logger.info("%s успешно загружен.", nvda_name)
            except Exception:
                logger.exception("Ошибка загрузки NVDA Controller (%s).", nvda_name)
        else:
            logger.warning("Файл %s не найден в папке speech.", nvda_name)

    def speak(self, text: str, interrupt: bool = True, config: dict = None):
        """Произносит текст через NVDA или SAPI5 в фоновом потоке."""
        if not config:
            return

        if not config.get("enable_speech", False):
            return

        if not text:
            if interrupt and self.nvda:
                self.nvda.nvdaController_cancelSpeech()
            return

        def _say():
            time.sleep(0.05)

            spoken_by_sr = False

            if self.nvda:
                try:
                    if interrupt:
                        self.nvda.nvdaController_cancelSpeech()
                    res = self.nvda.nvdaController_speakText(ctypes.c_wchar_p(str(text)))
                    if res == 0:
                        spoken_by_sr = True
                except Exception:
                    logger.exception("Ошибка при выводе речи через NVDA.")

            if not spoken_by_sr and config.get("enable_sapi5", False):
                pythoncom.CoInitialize()
                try:
                    speaker = win32com.client.Dispatch("SAPI.SpVoice")
                    flags = 3 if interrupt else 1
                    speaker.Speak(str(text), flags)
                except Exception:
                    logger.exception("Ошибка вывода речи через SAPI5.")
                finally:
                    pythoncom.CoUninitialize()

        threading.Thread(target=_say, daemon=True).start()


# Глобальный объект для управления библиотеками озвучивания
speech_manager = SpeechManager()

def speak(text, interrupt=True, config=None):
    speech_manager.speak(text, interrupt, config)
