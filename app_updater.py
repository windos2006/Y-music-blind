# Year: 2026
"""Модуль автообновления y-music-blind для основной программы.

Проверяет последний релиз через GitHub API, показывает список изменений,
скачивает ZIP-архив с индикацией прогресса/скорости/времени и запускает
внешний модуль updater.exe из временной папки, после чего закрывает
основное приложение.

Интеграция:
    from app_updater import UpdateDialog
    dlg = UpdateDialog(parent, current_version=VERSION)
    dlg.ShowModal()
    dlg.Destroy()
"""

import os
import sys
import json
import time
import shutil
import tempfile
import threading
import urllib.request
import subprocess
import logging
import wx

from packaging import version
from version import VERSION

logger = logging.getLogger(__name__)

DEFAULT_REPO_OWNER = "windos2006"
DEFAULT_REPO_NAME = "Y-music-blind"


def get_main_exe_name() -> str:
    """Возвращает имя главного исполняемого файла."""
    if getattr(sys, 'frozen', False):
        return os.path.basename(sys.executable)
    return "main.py"


class UpdateCore:
    """Логика работы с GitHub API, загрузки архива и запуска апдейтера."""

    def __init__(self, current_version: str,
                 repo_owner: str = DEFAULT_REPO_OWNER,
                 repo_name: str = DEFAULT_REPO_NAME):
        self.current_version = current_version
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.api_url = (f"https://api.github.com/repos/{repo_owner}/"
                        f"{repo_name}/releases/latest")
        self.cancel_requested = False

    def check_for_updates(self):
        """Запрашивает свежий релиз с GitHub API.

        Возвращает tuple:
        (has_update, version_tag, release_notes, download_url, download_filename)
        """
        req = urllib.request.Request(
            self.api_url,
            headers={"User-Agent": "Y-Music-Blind-Updater/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    latest_tag = str(data.get("tag_name", "")).lstrip("v")
                    notes = data.get("body", "Описание изменений отсутствует.")

                    download_url = None
                    download_filename = "y-music-blind.zip"
                    for asset in data.get("assets", []):
                        name = asset.get("name", "")
                        if name.lower().endswith(".zip"):
                            download_url = asset.get("browser_download_url")
                            download_filename = name or "y-music-blind.zip"
                            break

                    if (latest_tag and
                            version.parse(latest_tag) >
                            version.parse(self.current_version)):
                        return True, latest_tag, notes, download_url, download_filename
        except Exception as e:
            logger.exception("Ошибка проверки обновлений")

        return False, None, None, None, None

    def download_file(self, url: str, dest_path: str, progress_callback):
        """Скачивает ZIP-архив с расчётом скорости и времени."""
        self.cancel_requested = False
        req = urllib.request.Request(
            url, headers={"User-Agent": "Y-Music-Blind-Updater/1.0"},
        )

        try:
            with urllib.request.urlopen(req) as response, \
                    open(dest_path, "wb") as out_file:
                total_length = int(response.headers.get('content-length', 0))
                downloaded = 0
                block_size = 16384
                start_time = time.time()

                while True:
                    if self.cancel_requested:
                        out_file.close()
                        if os.path.exists(dest_path):
                            os.remove(dest_path)
                        return False

                    buffer = response.read(block_size)
                    if not buffer:
                        break

                    downloaded += len(buffer)
                    out_file.write(buffer)

                    elapsed = time.time() - start_time
                    speed = (downloaded / elapsed) if elapsed > 0 else 0.0
                    speed_mbps = speed / (1024 * 1024)
                    eta_seconds = (total_length - downloaded) / speed if speed > 0 else 0

                    percent = int((downloaded / total_length) * 100) if total_length > 0 else 0

                    if progress_callback:
                        progress_callback(percent, speed_mbps, eta_seconds,
                                          downloaded, total_length)

            return True
        except Exception as e:
            logger.exception("Ошибка при скачивании обновления")
            if os.path.exists(dest_path):
                try:
                    os.remove(dest_path)
                except OSError:
                    pass
            return False

    def launch_updater_and_exit(self, zip_path: str, main_exe_name: str):
        """Копирует updater.exe в %TEMP%, запускает его и закрывает программу."""
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))

        local_updater = os.path.join(base_dir, "updater.exe")
        if not os.path.exists(local_updater):
            wx.MessageBox(
                f"Не найден исполняемый файл модуля обновления:\n{local_updater}\n\n"
                "Обновление не может быть завершено.",
                "Ошибка автообновления",
                wx.OK | wx.ICON_ERROR,
            )
            return

        temp_dir = tempfile.gettempdir()
        temp_updater = os.path.join(temp_dir, "Y_Music_Updater_Instance.exe")
        shutil.copy2(local_updater, temp_updater)

        cmd = [
            temp_updater,
            "--zip", zip_path,
            "--target", base_dir,
            "--exe", main_exe_name,
            "--pid", str(os.getpid()),
        ]

        creationflags = 0
        if os.name == 'nt':
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | \
                getattr(subprocess, 'DETACHED_PROCESS', 0)

        subprocess.Popen(cmd, creationflags=creationflags)
        sys.exit(0)


class UpdateDialog(wx.Dialog):
    """Графический диалог wxPython для проверки и установки обновлений."""

    def __init__(self, parent, current_version: str = None,
                 main_exe_name: str = None):
        super().__init__(
            parent, title="Проверка обновлений", size=(580, 460),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.TAB_TRAVERSAL,
        )

        self.current_version = current_version or VERSION
        self.main_exe_name = main_exe_name or get_main_exe_name()
        self.core = UpdateCore(self.current_version)
        self.download_url = None
        self.download_filename = "y-music-blind.zip"
        self.downloaded_zip_path = None

        self._init_ui()
        self.CentreOnParent()
        wx.CallAfter(self.start_check)

    def _init_ui(self):
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        self.lbl_status = wx.StaticText(self, label="Проверка наличия новых версий...")
        font = self.lbl_status.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        self.lbl_status.SetFont(font)
        main_sizer.Add(self.lbl_status, 0, wx.ALL | wx.EXPAND, 10)

        main_sizer.Add(wx.StaticText(self, label="Информация о версии и список изменений:"),
                       0, wx.LEFT | wx.RIGHT, 10)
        self.txt_notes = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        main_sizer.Add(self.txt_notes, 1, wx.ALL | wx.EXPAND, 10)

        self.lbl_progress_info = wx.StaticText(self, label="Ожидание действий...")
        main_sizer.Add(self.lbl_progress_info, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 10)

        self.gauge = wx.Gauge(self, range=100)
        main_sizer.Add(self.gauge, 0, wx.ALL | wx.EXPAND, 10)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_action = wx.Button(self, label="Загрузить и установить")
        self.btn_action.Enable(False)
        self.btn_action.Bind(wx.EVT_BUTTON, self.on_action_click)
        btn_sizer.Add(self.btn_action, 0, wx.RIGHT, 10)

        self.btn_close = wx.Button(self, id=wx.ID_CANCEL, label="Закрыть")
        self.btn_close.Bind(wx.EVT_BUTTON, self.on_close_click)
        btn_sizer.Add(self.btn_close, 0)

        main_sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        self.SetSizer(main_sizer)

    def start_check(self):
        threading.Thread(target=self._async_check, daemon=True).start()

    def _async_check(self):
        has_update, latest_tag, notes, url, filename = self.core.check_for_updates()
        wx.CallAfter(self._on_check_finished, has_update, latest_tag, notes, url, filename)

    def _on_check_finished(self, has_update, latest_tag, notes, url, filename):
        if has_update and url:
            self.download_url = url
            self.download_filename = filename or "y_music_update_release.zip"
            self.lbl_status.SetLabel(
                f"Доступно обновление: {latest_tag} "
                f"(Текущая версия: {self.current_version})")
            self.txt_notes.SetValue(notes or "Описание изменений отсутствует.")
            self.btn_action.Enable(True)
            self.btn_action.SetFocus()
        elif has_update and not url:
            self.lbl_status.SetLabel("Доступна новая версия, но в релизе нет ZIP-архива.")
            self.txt_notes.SetValue("Обновитесь вручную через страницу релизов.")
        else:
            self.lbl_status.SetLabel("У вас установлена последняя версия программы.")
            self.txt_notes.SetValue("Обновлений не обнаружено.")
            self.btn_action.Enable(False)

    def on_action_click(self, event):
        if self.downloaded_zip_path and os.path.exists(self.downloaded_zip_path):
            self.core.launch_updater_and_exit(self.downloaded_zip_path, self.main_exe_name)
            return

        self.btn_action.Enable(False)
        self.btn_close.SetLabel("Отмена")
        self.lbl_status.SetLabel("Загрузка пакета обновления...")

        temp_dir = tempfile.gettempdir()
        self.downloaded_zip_path = os.path.join(temp_dir, self.download_filename)

        threading.Thread(target=self._async_download, daemon=True).start()

    def _async_download(self):
        success = self.core.download_file(
            self.download_url,
            self.downloaded_zip_path,
            progress_callback=self._update_progress_ui,
        )
        wx.CallAfter(self._on_download_finished, success)

    def _update_progress_ui(self, percent, speed_mbps, eta_seconds, downloaded, total):
        def _update():
            self.gauge.SetValue(percent)
            info = (f"Прогресс: {percent}% | Скорость: {speed_mbps:.2f} МБ/с | "
                    f"Осталось: {int(eta_seconds)} сек.")
            self.lbl_progress_info.SetLabel(info)
        wx.CallAfter(_update)

    def _on_download_finished(self, success):
        if success:
            self.lbl_status.SetLabel("Загрузка завершена! Нажмите «Установить» для перезапуска.")
            self.lbl_progress_info.SetLabel("Пакет готов к распаковке.")
            self.btn_action.SetLabel("Установить и перезапустить")
            self.btn_action.Enable(True)
            self.btn_action.SetFocus()
            self.btn_close.SetLabel("Закрыть")
        else:
            if self.core.cancel_requested:
                self.lbl_status.SetLabel("Загрузка отменена.")
            else:
                self.lbl_status.SetLabel("Произошла ошибка при загрузке.")
            self.btn_action.SetLabel("Загрузить и установить")
            self.btn_action.Enable(True)
            self.btn_close.SetLabel("Закрыть")

    def on_close_click(self, event):
        self.core.cancel_requested = True
        self.EndModal(wx.ID_CANCEL)


if __name__ == "__main__":
    app = wx.App(False)
    dlg = UpdateDialog(parent=None, current_version=VERSION)
    dlg.ShowModal()
    dlg.Destroy()
    app.MainLoop()
