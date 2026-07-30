# Year: 2026
"""Вспомогательные утилиты для работы с путями и ресурсами.

Учитывает особенности упаковки PyInstaller: в скомпилированном EXE
пути к ресурсам берутся из sys._MEIPASS (временная папка распаковки).
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