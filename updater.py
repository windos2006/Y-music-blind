# Year: 2026
"""Автономный модуль установки обновлений y-music-blind.

Запускается из системной временной папки (%TEMP%), дожидается завершения
основного процесса, выборочно распаковывает ZIP-архив поверх старых файлов
(сохраняя пользовательские настройки) и запускает обновлённое приложение.

Сборка: pyinstaller --onefile --noconsole updater.py
"""

import os
import sys
import time
import zipfile
import argparse
import subprocess
import ctypes


# Расширения файлов, которые НЕЛЬЗЯ перезаписывать, если файл уже существует
# на компьютере пользователя (настройки, базы, логи и т.п.).
PROTECTED_EXTENSIONS = {
    '.ini', '.json', '.dat', '.db', '.sqlite', '.cfg', '.conf',
    '.sav', '.txt', '.log',
}

# Конкретные имена файлов, которые тоже защищены от перезаписи.
PROTECTED_FILENAMES = {
    'config.json',
    'accounts.json',
    'auth_data.json',
    'settings.ini',
    'options.dat',
}


def is_process_running(pid: int, exe_name: str = "") -> bool:
    """Проверяет, активен ли процесс по PID или через tasklist."""
    try:
        SYNCHRONIZE = 0x00100000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
    except Exception:
        pass

    try:
        output = subprocess.check_output(['tasklist', '/fo', 'csv'], encoding='utf-8', errors='ignore')
        output_lower = output.lower()
        if exe_name and exe_name.lower() in output_lower:
            return True
        if 'python.exe' in output_lower or 'y-music-blind' in output_lower:
            return True
    except Exception:
        pass

    return False


def is_protected_file(rel_path: str, target_dir: str) -> bool:
    """True, если файл уже существует и защищён от перезаписи."""
    full_target_path = os.path.join(target_dir, rel_path)

    # Если файла у пользователя нет — распаковываем в любом случае.
    if not os.path.exists(full_target_path):
        return False

    filename = os.path.basename(rel_path).lower()
    _, ext = os.path.splitext(rel_path)
    ext = ext.lower()

    if filename in PROTECTED_FILENAMES:
        return True
    if ext in PROTECTED_EXTENSIONS:
        return True

    return False


def main():
    parser = argparse.ArgumentParser(
        description="Автономный модуль установки обновлений y-music-blind.",
    )
    parser.add_argument("--zip", required=True, help="Путь к скачанному zip-архиву")
    parser.add_argument("--target", required=True, help="Целевая директория установки")
    parser.add_argument("--exe", required=True, help="Имя главного исполняемого файла")
    parser.add_argument("--pid", type=int, required=True, help="PID основного процесса")
    args = parser.parse_args()

    # 1. Ждём завершения основного процесса.
    print("Ожидание закрытия основной программы...")
    while is_process_running(args.pid, args.exe):
        time.sleep(0.5)

    # Небольшая задержка, чтобы ОС сняла блокировки с файлов (_internal).
    time.sleep(1.5)

    # 2. Выборочная распаковка архива с сохранением настроек пользователя.
    print("Распаковка обновления и замена файлов...")
    try:
        with zipfile.ZipFile(args.zip, 'r') as zip_ref:
            for member in zip_ref.infolist():
                if member.is_dir():
                    continue
                # Защита от path traversal (../.. в именах внутри архива).
                rel = member.filename.replace('\\', '/')
                if rel.startswith('/') or '..' in rel.split('/'):
                    print(f"[Пропуск] Недопустимый путь в архиве: {member.filename}")
                    continue
                if is_protected_file(rel, args.target):
                    print(f"[Защита] Сохранён файл пользователя: {rel}")
                    continue
                zip_ref.extract(member, args.target)
        print("Все файлы успешно обновлены, настройки пользователя сохранены.")
    except Exception as e:
        print(f"Ошибка при распаковке файлов: {e}")
        time.sleep(5)
        sys.exit(1)

    # 3. Удаляем временный zip-архив.
    try:
        if os.path.exists(args.zip):
            os.remove(args.zip)
    except Exception:
        pass

    # 4. Запускаем обновлённое приложение.
    target_exe = os.path.join(args.target, args.exe)
    if os.path.exists(target_exe):
        if args.exe.lower().endswith('.py'):
            subprocess.Popen([sys.executable, target_exe], cwd=args.target)
        else:
            subprocess.Popen([target_exe], cwd=args.target)
        print(f"Обновлённая версия запущена: {target_exe}")
    else:
        print(f"Ошибка: Исполняемый файл {target_exe} не найден.")
        time.sleep(5)


if __name__ == "__main__":
    main()
