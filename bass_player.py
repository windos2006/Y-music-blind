# Year: 2026
import ctypes
import os
import sys
import logging
from utils import get_resource_path

logger = logging.getLogger(__name__)

class BassPlayer:
    """Управление воспроизведением аудио через BASS библиотеку.

    Предоставляет методы для воспроизведения URL-потоков, паузы,
    регулировки громкости и перемотки. Использует ctypes для
    взаимодействия с нативной BASS DLL.
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
        self.BASS_POS_BYTE = 0
        
        self.BASS_ACTIVE_STOPPED = 0
        self.BASS_ACTIVE_PLAYING = 1
        self.BASS_ACTIVE_STALLED = 2
        self.BASS_ACTIVE_PAUSED = 3

        self.bass.BASS_Init.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        self.bass.BASS_Init.restype = ctypes.c_bool
        self.bass.BASS_StreamCreateURL.argtypes = [ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        self.bass.BASS_StreamCreateURL.restype = ctypes.c_uint
        self.bass.BASS_ChannelPlay.argtypes = [ctypes.c_uint, ctypes.c_bool]
        self.bass.BASS_ChannelPause.argtypes = [ctypes.c_uint]
        self.bass.BASS_ChannelSetAttribute.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_float]
        self.bass.BASS_ChannelGetPosition.argtypes = [ctypes.c_uint, ctypes.c_uint]
        self.bass.BASS_ChannelGetPosition.restype = ctypes.c_uint64
        self.bass.BASS_ChannelSetPosition.argtypes = [ctypes.c_uint, ctypes.c_uint64, ctypes.c_uint]
        self.bass.BASS_ChannelBytes2Seconds.argtypes = [ctypes.c_uint, ctypes.c_uint64]
        self.bass.BASS_ChannelBytes2Seconds.restype = ctypes.c_double
        self.bass.BASS_ChannelSeconds2Bytes.argtypes = [ctypes.c_uint, ctypes.c_double]
        self.bass.BASS_ChannelSeconds2Bytes.restype = ctypes.c_uint64
        self.bass.BASS_ChannelIsActive.argtypes = [ctypes.c_uint]
        self.bass.BASS_ChannelIsActive.restype = ctypes.c_int
        self.bass.BASS_StreamFree.argtypes = [ctypes.c_uint]

        if not self.bass.BASS_Init(self.BASS_INIT_DEVICE, 44100, 0, 0, 0):
            logger.warning("BASS уже инициализирован или произошла ошибка.")

        self.stream = 0
        self.volume = 1.0

    def play_url(self, url: str) -> None:
        """Начинает воспроизведение аудиопотока по URL."""
        if self.stream:
            self.bass.BASS_StreamFree(self.stream)
        
        self.stream = self.bass.BASS_StreamCreateURL(url.encode('utf-8'), 0, self.BASS_STREAM_AUTOFREE, 0, 0)
        if self.stream:
            self.bass.BASS_ChannelSetAttribute(self.stream, self.BASS_ATTRIB_VOL, ctypes.c_float(self.volume))
            self.bass.BASS_ChannelPlay(self.stream, False)

    def toggle_pause(self) -> None:
        """Переключает между воспроизведением и паузой."""
        if not self.stream: 
            return
        active = self.bass.BASS_ChannelIsActive(self.stream)
        if active == self.BASS_ACTIVE_PLAYING:
            self.bass.BASS_ChannelPause(self.stream)
        elif active == self.BASS_ACTIVE_PAUSED:
            self.bass.BASS_ChannelPlay(self.stream, False)

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

    def seek(self, seconds_delta: float) -> None:
        """Перематывает на указанное количество секунд (+/–)."""
        if not self.stream: 
            return
        pos_bytes = self.bass.BASS_ChannelGetPosition(self.stream, self.BASS_POS_BYTE)
        current_sec = self.bass.BASS_ChannelBytes2Seconds(self.stream, pos_bytes)
        
        new_sec = max(0.0, current_sec + seconds_delta)
        new_bytes = self.bass.BASS_ChannelSeconds2Bytes(self.stream, new_sec)
        self.bass.BASS_ChannelSetPosition(self.stream, new_bytes, self.BASS_POS_BYTE)
