# Year: 2026
import ctypes
import os
import logging
from utils import get_resource_path

logger = logging.getLogger(__name__)

# Константы BASS_FX (см. bass_fx.chm — официальную документацию)
BASS_FX_BFX_PEAKEQ = 0x10004      # Тип эффекта «эквалайзер» (BiQuad peaking EQ)
BASS_BFX_CHANALL = -1             # Применять ко всем каналам звука
BASS_ATTRIB_TEMPO = 0x10000       # Темп воспроизведения (без изменения тона)

# Флаги
BASS_STREAM_DECODE = 0x200000     # Поток только для декодирования (без вывода) — внимание: 0x100000 это BASS_STREAM_BLOCK
BASS_FX_FREESOURCE = 0x10000      # Освобождать исходный поток вместе с эффектным

# Коды ошибок BASS (BASS_ErrorGetCode возвращает int)
BASS_ERRORS = {
    0:  "OK",
    1:  "Память",
    2:  "Файл не открыт",
    3:  "Драйвер",
    4:  "Буфер потерян",
    5:  "Дескриптор",
    6:  "Формат",
    7:  "Позиция",
    8:  "Не инициализирован",
    9:  "Не запущен",
    10: "SSL",
    11: "Переподключение",
    12: "Устройство",
    13: "Нет EAX",
    14: "Тайм-аут",
    15: "Формат файла",
    16: "Динамик",
    17: "Версия",
    18: "Кодек",
    19: "Завершён",
    20: "Занят",
    21: "Не BASS",
}


def bass_error_text(code=None):
    """Возвращает текстовое описание ошибки BASS по коду (или текущему)."""
    if code is None:
        code = -1  # вызывающий код сам должен получить код
    return BASS_ERRORS.get(code, f"Неизвестная ошибка ({code})")

# Пресеты эквалайзера: пять полос с центрами 60, 230, 910, 3600 и 14000 Гц.
# Каждое значение — усиление полосы в децибелах.
EQUALIZER_PRESETS = {
    "Поп":           [1.0, 2.0, 3.0, 1.0, 0.0],
    "Рок":           [4.0, 1.0, -2.0, 1.0, 3.0],
    "Техно":         [2.0, 1.0, 0.0, 2.0, 0.0],
    "Софт-рок":      [0.0, 2.0, 3.0, 2.0, 0.0],
    "Классика":      [4.0, 0.0, -2.0, 0.0, 2.0],
    "Электронная":   [4.0, 0.0, -2.0, 2.0, 4.0],
    "Клубная вечеринка": [2.0, 0.0, 0.0, 2.0, 0.0],
}

# Центральные частоты полос в герцах
EQ_FREQUENCIES = [60.0, 230.0, 910.0, 3600.0, 14000.0]

# Границы скорости воспроизведения (множитель: 1.0 — нормальная)
MIN_SPEED = 0.25
MAX_SPEED = 3.0
SPEED_STEP = 0.05


class BassPlayer:
    """Управление воспроизведением аудио через BASS библиотеку.

    Предоставляет методы для воспроизведения URL-потоков, паузы,
    регулировки громкости, перемотки, управления скоростью (темпом)
    и настоящего эквалайзера на основе BASS_FX. Использует ctypes
    для взаимодействия с нативными DLL BASS и BASS_FX.

    Если загрузить bass_fx.dll не удалось, эквалайзер и изменение
    темпа автоматически становятся недоступными, но обычное
    воспроизведение продолжает работать.
    """

    def __init__(self):
        dll_path = get_resource_path('bass.dll')

        try:
            if hasattr(os, 'add_dll_directory'):
                base_dir = os.path.dirname(dll_path)
                os.add_dll_directory(base_dir)
            self.bass = ctypes.WinDLL(dll_path)
            logger.info("Библиотека BASS успешно загружена.")
        except Exception as e:
            logger.critical("Не удалось загрузить bass.dll.", exc_info=True)
            raise RuntimeError(
                f"Не удалось загрузить bass.dll.\n"
                f"Убедись, что ты взял 64-битную версию библиотеки (из папки x64 в архиве BASS). "
                f"Ошибка: {e}"
            )

        self.BASS_INIT_DEVICE = -1
        self.BASS_STREAM_AUTOFREE = 0x40000
        self.BASS_ATTRIB_VOL = 2
        self.BASS_ATTRIB_FREQ = 1
        self.BASS_POS_BYTE = 0

        self.BASS_ACTIVE_STOPPED = 0
        self.BASS_ACTIVE_PLAYING = 1
        self.BASS_ACTIVE_STALLED = 2
        self.BASS_ACTIVE_PAUSED = 3

        self._declare_bass_funcs()

        if not self.bass.BASS_Init(self.BASS_INIT_DEVICE, 44100, 0, 0, 0):
            logger.warning("BASS уже инициализирован или произошла ошибка.")

        # Попытка загрузить BASS_FX для эквалайзера и темпа
        self.bass_fx = self._load_bass_fx()
        self.fx_available = self.bass_fx is not None
        if not self.fx_available:
            logger.warning("bass_fx.dll не загружена — эквалайзер и изменение темпа недоступны.")

        self.stream = 0
        self._source_stream = 0          # Исходный (декодирующий) поток для темпа
        self._base_freq = 44100          # Базовая частота потока для запасного метода скорости
        self.volume = 1.0
        self.speed = 1.0
        self.current_device = -1
        self.equalizer_enabled = False
        self.equalizer_preset = None
        self._eq_handle = 0              # Дескриптор эффекта эквалайзера (первая полоса)
        self._eq_handles = []            # Дескрипторы всех полос эквалайзера

    def _declare_bass_funcs(self):
        """Объявляет типы аргументов и возвращаемых значений функций BASS."""
        b = self.bass
        b.BASS_Init.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        b.BASS_Init.restype = ctypes.c_bool
        b.BASS_StreamCreateURL.argtypes = [ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        b.BASS_StreamCreateURL.restype = ctypes.c_uint
        b.BASS_ChannelPlay.argtypes = [ctypes.c_uint, ctypes.c_bool]
        b.BASS_ChannelPlay.restype = ctypes.c_bool
        b.BASS_ChannelPause.argtypes = [ctypes.c_uint]
        b.BASS_ChannelPause.restype = ctypes.c_bool
        b.BASS_ChannelStop.argtypes = [ctypes.c_uint]
        b.BASS_ChannelStop.restype = ctypes.c_bool
        b.BASS_ChannelSetAttribute.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_float]
        b.BASS_ChannelSetAttribute.restype = ctypes.c_bool
        b.BASS_ChannelGetAttribute.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_float)]
        b.BASS_ChannelGetAttribute.restype = ctypes.c_bool
        b.BASS_ChannelGetPosition.argtypes = [ctypes.c_uint, ctypes.c_uint]
        b.BASS_ChannelGetPosition.restype = ctypes.c_uint64
        b.BASS_ChannelSetPosition.argtypes = [ctypes.c_uint, ctypes.c_uint64, ctypes.c_uint]
        b.BASS_ChannelSetPosition.restype = ctypes.c_bool
        b.BASS_ChannelBytes2Seconds.argtypes = [ctypes.c_uint, ctypes.c_uint64]
        b.BASS_ChannelBytes2Seconds.restype = ctypes.c_double
        b.BASS_ChannelSeconds2Bytes.argtypes = [ctypes.c_uint, ctypes.c_double]
        b.BASS_ChannelSeconds2Bytes.restype = ctypes.c_uint64
        b.BASS_ChannelGetLength.argtypes = [ctypes.c_uint, ctypes.c_uint]
        b.BASS_ChannelGetLength.restype = ctypes.c_uint64
        b.BASS_ChannelIsActive.argtypes = [ctypes.c_uint]
        b.BASS_ChannelIsActive.restype = ctypes.c_int
        b.BASS_StreamFree.argtypes = [ctypes.c_uint]
        b.BASS_StreamFree.restype = ctypes.c_bool
        b.BASS_ChannelSetDevice.argtypes = [ctypes.c_uint, ctypes.c_int]
        b.BASS_ChannelSetDevice.restype = ctypes.c_bool
        b.BASS_SetDevice.argtypes = [ctypes.c_int]
        b.BASS_SetDevice.restype = ctypes.c_bool
        b.BASS_ChannelSetFX.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_int]
        b.BASS_ChannelSetFX.restype = ctypes.c_uint
        b.BASS_ChannelRemoveFX.argtypes = [ctypes.c_uint, ctypes.c_uint]
        b.BASS_ChannelRemoveFX.restype = ctypes.c_bool
        b.BASS_FXSetParameters.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        b.BASS_FXSetParameters.restype = ctypes.c_bool
        b.BASS_ErrorGetCode.argtypes = []
        b.BASS_ErrorGetCode.restype = ctypes.c_int

    def _load_bass_fx(self):
        """Загружает bass_fx.dll и инициализирует её вызовом BASS_FX_GetVersion.

        Returns:
            ctypes.WinDLL или None, если загрузка не удалась.
        """
        try:
            fx_path = get_resource_path('bass_fx.dll')
            if not os.path.exists(fx_path):
                logger.warning("bass_fx.dll не найден рядом с bass.dll.")
                return None
            bass_fx = ctypes.WinDLL(fx_path)
            bass_fx.BASS_FX_GetVersion.argtypes = []
            bass_fx.BASS_FX_GetVersion.restype = ctypes.c_uint
            version = bass_fx.BASS_FX_GetVersion()
            logger.info("bass_fx.dll загружена, версия %d.", version)

            # Объявление сигнатур функций BASS_FX (см. bass_fx.chm).
            # BASS_FX_TempoCreate — экспорт bass_fx.dll; BASS_FXSetParameters —
            # функция ядра BASS (объявлена в _declare_bass_funcs).
            bass_fx.BASS_FX_TempoCreate.argtypes = [ctypes.c_uint, ctypes.c_uint]
            bass_fx.BASS_FX_TempoCreate.restype = ctypes.c_uint
            return bass_fx
        except Exception:
            logger.exception("Ошибка загрузки bass_fx.dll.")
            return None

    def play_url(self, url: str) -> bool:
        """Начинает воспроизведение аудиопотока по URL.

        Если доступна BASS_FX, поток создаётся через BASS_FX_TempoCreate —
        это позволяет менять скорость без изменения тона. Если нет —
        используется обычный поток, а скорость меняется частотой.

        Возвращает True, если поток создан и запущен.
        """
        self._free_stream()
        self._source_stream = 0

        if self.current_device != -1:
            try:
                self.bass.BASS_SetDevice(self.current_device)
            except Exception:
                logger.exception("Не удалось выбрать устройство BASS.")

        try:
            if self.fx_available:
                source = self.bass.BASS_StreamCreateURL(url.encode('utf-8'), 0, BASS_STREAM_DECODE, 0, 0)
                if source:
                    self._source_stream = source
                    self.stream = self.bass_fx.BASS_FX_TempoCreate(source, BASS_FX_FREESOURCE)
                    if not self.stream:
                        logger.error("BASS_FX_TempoCreate не удался, код ошибки %d.",
                                     self.bass.BASS_ErrorGetCode(), stack_info=True)
                        # Падаем на обычный поток, если темп недоступен
                        self.bass.BASS_StreamFree(source)
                        self._source_stream = 0
                else:
                    logger.error("Не удалось создать декодирующий поток, код ошибки %d.",
                                 self.bass.BASS_ErrorGetCode(), stack_info=True)
            if not self.stream:
                self.stream = self.bass.BASS_StreamCreateURL(url.encode('utf-8'), 0,
                                                             self.BASS_STREAM_AUTOFREE, 0, 0)
        except Exception:
            logger.exception("Ошибка при создании потока воспроизведения.")
            self.stream = 0

        if not self.stream:
            logger.error("Не удалось создать поток для воспроизведения, код ошибки %d.",
                         self.bass.BASS_ErrorGetCode(), stack_info=True)
            return False

        if self.current_device != -1:
            try:
                self.bass.BASS_ChannelSetDevice(self.stream, self.current_device)
            except Exception:
                logger.exception("Не удалось перенаправить поток на устройство BASS.")
        self._read_base_freq()
        self.bass.BASS_ChannelSetAttribute(self.stream, self.BASS_ATTRIB_VOL, ctypes.c_float(self.volume))
        self._apply_speed_to_stream()
        if self.equalizer_enabled and self.fx_available:
            self._apply_equalizer()
        self.bass.BASS_ChannelPlay(self.stream, False)
        return True

    def _free_stream(self):
        """Освобождает текущий поток (вместе с исходным при темпе).

        При BASS_FX_FREESOURCE освобождение темпового потока автоматически
        освобождает и исходный, поэтому не вызываем BASS_StreamFree для
        _source_stream повторно.
        """
        if self.stream:
            if self._source_stream and self._source_stream != self.stream:
                self.bass.BASS_StreamFree(self._source_stream)
            self.bass.BASS_StreamFree(self.stream)
            self.stream = 0
            self._source_stream = 0
        elif self._source_stream:
            self.bass.BASS_StreamFree(self._source_stream)
            self._source_stream = 0
        self._eq_handle = 0
        self._eq_handles = []

    def _read_base_freq(self):
        """Запоминает номинальную частоту потока для запасного изменения скорости."""
        try:
            value = ctypes.c_float()
            if self.bass.BASS_ChannelGetAttribute(self.stream, self.BASS_ATTRIB_FREQ, ctypes.byref(value)):
                if value.value > 0:
                    self._base_freq = value.value
        except Exception:
            self._base_freq = 44100

    def toggle_pause(self) -> None:
        """Переключает между воспроизведением и паузой."""
        if not self.stream:
            return
        active = self.bass.BASS_ChannelIsActive(self.stream)
        if active == self.BASS_ACTIVE_PLAYING:
            self.bass.BASS_ChannelPause(self.stream)
        elif active == self.BASS_ACTIVE_PAUSED:
            self.bass.BASS_ChannelPlay(self.stream, False)

    def stop(self) -> None:
        """Полностью останавливает воспроизведение и освобождает поток."""
        if self.stream:
            self.bass.BASS_ChannelStop(self.stream)
        self._free_stream()

    def get_state(self) -> int:
        """Возвращает состояние воспроизведения (0=стоп, 1=играет, 3=пауза)."""
        if not self.stream:
            return self.BASS_ACTIVE_STOPPED
        return self.bass.BASS_ChannelIsActive(self.stream)

    def set_volume(self, vol: float) -> None:
        """Устанавливает громкость (0.0 – 1.0)."""
        self.volume = max(0.0, min(1.0, vol))
        if self.stream:
            self.bass.BASS_ChannelSetAttribute(self.stream, self.BASS_ATTRIB_VOL, ctypes.c_float(self.volume))

    def get_volume(self) -> float:
        """Возвращает текущую громкость (0.0 – 1.0)."""
        return self.volume

    def set_speed(self, speed: float) -> float:
        """Устанавливает скорость воспроизведения (множитель, 1.0 — нормальная).

        На темповом потоке (BASS_FX) меняет темп без изменения тона,
        на обычном — частоту дискретизации (тон тоже меняется).
        Возвращает применённое значение.
        """
        speed = max(MIN_SPEED, min(MAX_SPEED, speed))
        self.speed = speed
        if not self.stream:
            return self.speed
        self._apply_speed_to_stream()
        return self.speed

    def _apply_speed_to_stream(self):
        """Применяет текущую скорость к активному потоку.

        Темповый поток (созданный BASS_FX_TempoCreate) хранит исходный
        поток в _source_stream; на нём скорость задаётся атрибутом TEMPO
        без изменения тона. На обычном потоке скорость меняется частотой.
        """
        if not self.stream:
            return
        if self._source_stream:
            self.bass.BASS_ChannelSetAttribute(self.stream, BASS_ATTRIB_TEMPO,
                                               ctypes.c_float((self.speed - 1.0) * 100.0))
        else:
            self.bass.BASS_ChannelSetAttribute(self.stream, self.BASS_ATTRIB_FREQ,
                                               ctypes.c_float(self._base_freq * self.speed))

    def change_speed(self, delta: float) -> float:
        """Меняет скорость на delta и возвращает новое значение."""
        return self.set_speed(self.speed + delta)

    def reset_speed(self) -> float:
        """Сбрасывает скорость до нормальной (1.0) и возвращает её."""
        return self.set_speed(1.0)

    def get_speed(self) -> float:
        """Возвращает текущую скорость воспроизведения."""
        return self.speed

    def get_position(self) -> float:
        """Возвращает текущую позицию воспроизведения в секундах."""
        if not self.stream:
            return 0.0
        pos_bytes = self.bass.BASS_ChannelGetPosition(self.stream, self.BASS_POS_BYTE)
        return self.bass.BASS_ChannelBytes2Seconds(self.stream, pos_bytes)

    def get_duration(self) -> float:
        """Возвращает общую длительность текущего трека в секундах."""
        if not self.stream:
            return 0.0
        len_bytes = self.bass.BASS_ChannelGetLength(self.stream, self.BASS_POS_BYTE)
        return self.bass.BASS_ChannelBytes2Seconds(self.stream, len_bytes)

    def seek(self, seconds_delta: float) -> None:
        """Перематывает на указанное количество секунд (+/–)."""
        if not self.stream:
            return
        pos_bytes = self.bass.BASS_ChannelGetPosition(self.stream, self.BASS_POS_BYTE)
        current_sec = self.bass.BASS_ChannelBytes2Seconds(self.stream, pos_bytes)

        new_sec = max(0.0, current_sec + seconds_delta)
        new_bytes = self.bass.BASS_ChannelSeconds2Bytes(self.stream, new_sec)
        self.bass.BASS_ChannelSetPosition(self.stream, new_bytes, self.BASS_POS_BYTE)

    def get_devices(self):
        """Возвращает список доступных аудиоустройств вывода: [(index, name), ...]."""
        devices = []

        class BASS_DEVICEINFO(ctypes.Structure):
            _fields_ = [
                ('name', ctypes.c_char_p),
                ('driver', ctypes.c_char_p),
                ('flags', ctypes.c_uint)
            ]

        self.bass.BASS_GetDeviceInfo.argtypes = [ctypes.c_int, ctypes.POINTER(BASS_DEVICEINFO)]
        self.bass.BASS_GetDeviceInfo.restype = ctypes.c_bool

        i = 0
        while True:
            info = BASS_DEVICEINFO()
            if not self.bass.BASS_GetDeviceInfo(i, ctypes.byref(info)):
                break
            name = info.name.decode('utf-8', errors='ignore') if info.name else f'Устройство {i}'
            if i > 0:
                devices.append((i, name))
            i += 1
        return devices

    # ── Эквалайзер ────────────────────────────────────────────────────────────

    class _BASS_BFX_PEAKEQ(ctypes.Structure):
        """Структура параметров эффекта эквалайзера (см. bass_fx.chm)."""
        _fields_ = [
            ('lBand', ctypes.c_int),       # Номер полосы (0..n)
            ('fBandwidth', ctypes.c_float),# Ширина полосы в октавах (0.1..<10)
            ('fQ', ctypes.c_float),        # Добротность (0..1), используется, если ширина не задана
            ('fCenter', ctypes.c_float),   # Центральная частота, Гц (1 Гц .. <половина частоты потока)
            ('fGain', ctypes.c_float),     # Усиление, дБ (-15..+15)
            ('lChannel', ctypes.c_int),    # Затронутые каналы (BASS_BFX_CHANALL = все)
        ]

    def _apply_equalizer(self):
        """Применяет пресет эквалайзера к текущему потоку.

        Каждая полоса эквалайзера — это отдельный эффект PEAKEQ,
        добавляемый к потоку с нарастающим приоритетом. Для каждой полосы
        создаётся свой дескриптор, что позволяет независимо управлять
        усилением на частотах 60, 230, 910, 3600 и 14000 Гц.
        """
        if not self.stream or not self.fx_available:
            return
        self._remove_equalizer_effect()

        preset = self.equalizer_preset or "Поп"
        gains = EQUALIZER_PRESETS.get(preset, EQUALIZER_PRESETS["Поп"])

        self._eq_handles = []
        for i, center in enumerate(EQ_FREQUENCIES):
            gain = gains[i] if i < len(gains) else 0.0
            fx = self.bass.BASS_ChannelSetFX(self.stream, BASS_FX_BFX_PEAKEQ, i)
            if not fx:
                logger.warning("Не удалось создать полосу %d эквалайзера, код ошибки %d.",
                               i, self.bass.BASS_ErrorGetCode())
                continue
            self._eq_handles.append(fx)
            params = self._BASS_BFX_PEAKEQ(
                lBand=i,
                fBandwidth=1.0,
                fQ=0.0,
                fCenter=center,
                fGain=max(-15.0, min(15.0, gain)),
                lChannel=BASS_BFX_CHANALL,
            )
            if not self.bass.BASS_FXSetParameters(fx, ctypes.byref(params)):
                logger.warning("Не удалось задать полосу %d эквалайзера, код ошибки %d.",
                               i, self.bass.BASS_ErrorGetCode())

        if self._eq_handles:
            self._eq_handle = self._eq_handles[0]

    def _remove_equalizer_effect(self):
        """Снимает все полосы эквалайзера с потока."""
        if self.stream:
            for fx in self._eq_handles:
                self.bass.BASS_ChannelRemoveFX(self.stream, fx)
        self._eq_handles = []
        self._eq_handle = 0

    def set_equalizer_preset(self, preset_name: str):
        """Применяет пресет эквалайзера к текущему потоку."""
        if not self.equalizer_enabled:
            self.equalizer_enabled = True
        self.equalizer_preset = preset_name
        if preset_name not in EQUALIZER_PRESETS:
            logger.warning("Неизвестный пресет эквалайзера: %s", preset_name)
            return
        self._apply_equalizer()
        logger.info("Применен пресет эквалайзера: %s", preset_name)

    def set_equalizer_enabled(self, enabled: bool):
        """Включает или выключает эквалайзер целиком."""
        self.equalizer_enabled = enabled
        if enabled and self.stream and self.fx_available:
            self._apply_equalizer()
        elif not enabled:
            self._remove_equalizer_effect()

    def set_device(self, device_index: int):
        """Переключает устройство вывода звука на лету."""
        self.current_device = device_index
        try:
            self.bass.BASS_Init(device_index, 44100, 0, 0, 0)
            self.bass.BASS_SetDevice(device_index)
        except Exception:
            pass
        if self.stream:
            try:
                self.bass.BASS_ChannelSetDevice(self.stream, device_index)
            except Exception:
                pass
        logger.info("Устройство вывода BASS изменено на индекс %d.", device_index)
