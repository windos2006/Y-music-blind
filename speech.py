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


def _is_nvda_running() -> bool:
    """Проверяет, запущен ли процесс nvda.exe в системе (через tasklist и win32process)."""
    try:
        import subprocess
        output = subprocess.check_output(['tasklist', '/fi', 'imagename eq nvda.exe', '/fo', 'csv'], encoding='utf-8', errors='ignore')
        if 'nvda.exe' in output.lower():
            return True
    except Exception:
        pass

    try:
        import win32process
        import win32api
        import win32con
        pids = win32process.EnumProcesses()
        for pid in pids:
            if not pid:
                continue
            try:
                h_proc = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION | win32con.PROCESS_VM_READ, False, pid)
                modules = win32process.EnumProcessModules(h_proc)
                if modules:
                    mod_name = win32api.GetModuleFileNameEx(h_proc, modules[0])
                    if os.path.basename(mod_name).lower() == "nvda.exe":
                        win32api.CloseHandle(h_proc)
                        return True
                win32api.CloseHandle(h_proc)
            except Exception:
                pass
    except Exception as e:
        logger.debug("Не удалось проверить запуск NVDA через win32process: %s", e)

    return False


class SpeechManager:
    """Управление речевым выводом через NVDA и SAPI5.

    Пытается использовать NVDA Controller Client для озвучивания,
    при недоступности — падает на SAPI5 (если разрешено в конфиге).

    NVDA используется только если его процесс (nvda.exe) реально запущен:
    наличие DLL без запущенного диктора не гарантирует воспроизведение речи.
    """

    # Минимальный интервал между произнесениями для одной категории
    # (чтобы при быстром регулировании громкости/скорости голос не «заикался»)
    DEBOUNCE_MEDIA = 0.3   # для «media» — громкость, скорость, перемотка
    DEBOUNCE_OTHER = 0.05  # для остальных — короткий таймаут

    def __init__(self):
        self.nvda = None
        self.is_64bit = platform.architecture()[0] == '64bit'
        self._nvda_check_time = 0.0
        self._nvda_running = False
        self._last_speak_time = {}   # category -> time.time()
        self._last_speak_text = {}   # category -> str
        self._speak_lock = threading.Lock()
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

    def nvda_available(self) -> bool:
        """True, если DLL загружена и nvda.exe реально запущен.

        Результат проверки процесса кэшируется на несколько секунд,
        чтобы не перебирать процессы системы на каждое произнесение.
        """
        if not self.nvda:
            return False
        now = time.time()
        if now - self._nvda_check_time > 5.0:
            self._nvda_running = _is_nvda_running()
            self._nvda_check_time = now
        return self._nvda_running

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

        category = config.get("_category", "general")
        now = time.time()
        debounce = self.DEBOUNCE_MEDIA if category == "media" else self.DEBOUNCE_OTHER

        with self._speak_lock:
            last_time = self._last_speak_time.get(category, 0.0)
            last_text = self._last_speak_text.get(category, "")
            if now - last_time < debounce and text == last_text:
                return
            self._last_speak_time[category] = now
            self._last_speak_text[category] = text

        def _say():
            spoken_by_sr = False

            if self.nvda_available():
                try:
                    if interrupt:
                        self.nvda.nvdaController_cancelSpeech()
                    res = self.nvda.nvdaController_speakText(ctypes.c_wchar_p(str(text)))
                    if res == 0:
                        spoken_by_sr = True
                except Exception:
                    logger.exception("Ошибка при выводе речи через NVDA.")
            else:
                logger.debug("NVDA не запущен — озвучивание через SAPI5.")

            if not spoken_by_sr and config.get("enable_sapi5", False):
                try:
                    pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
                except Exception:
                    try:
                        pythoncom.CoInitialize()
                    except Exception:
                        pass
                try:
                    speaker = win32com.client.Dispatch("SAPI.SpVoice")
                    # Синхронное произнесение: голос успевает договорить до
                    # CoUninitialize в этом же потоке (async-режим обрывался).
                    # SPF_PURGEBEFORESPEAK (2) при прерывании, иначе SPF_DEFAULT (0).
                    flags = 2 if interrupt else 0
                    speaker.Speak(str(text), flags)
                except Exception:
                    logger.exception("Ошибка вывода речи через SAPI5.")
                finally:
                    try:
                        pythoncom.CoUninitialize()
                    except Exception:
                        pass

        threading.Thread(target=_say, daemon=True).start()


# Глобальный объект для управления библиотеками озвучивания
speech_manager = SpeechManager()

def speak(text, interrupt=True, config=None):
    speech_manager.speak(text, interrupt, config)
