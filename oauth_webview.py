# Year: 2026
"""Диалоги авторизации Яндекс.Музыки через встроенный WebView.

Предоставляет два способа:
- BrowserAuthDialog — классический OAuth с перехватом токена после редиректа.
- DeviceAuthDialog — привязка устройства через passport.yandex.ru.

Оба диалога перед загрузкой страницы очищают куки и историю WebView,
чтобы предыдущая сессия Яндекса не всплывала при повторной авторизации.

Для изоляции сессий каждая авторизация использует отдельную временную
папку для кук WebView2 (через WEBVIEW2_USER_DATA_FOLDER). После
закрытия диалога временная папка удаляется.
"""

import wx
import wx.html2
import logging
import urllib.parse
import os
import tempfile
import uuid
import ctypes
from ctypes import wintypes

logger = logging.getLogger(__name__)

# Константы WinInet для очистки кук IE-бэкенда
_INTERNET_OPTION_END_BROWSER_SESSION = 42
_INTERNET_OPTION_SUPPRESS_BEHAVIOR = 81
_INTERNET_SUPPRESS_COOKIE_PERSIST = 3


def _suppress_cookie_persistence_wininet():
    """Подавляет сохранение кук на уровне WinInet (IE-бэкенд WebView).

    Вызов InternetSetOption с INTERNET_OPTION_SUPPRESS_BEHAVIOR
    предотвращает запись кук на диск в текущем процессе.
    """
    try:
        internet_dll = ctypes.windll.wininet
        internet_dll.InternetSetOptionW(
            None,
            _INTERNET_OPTION_SUPPRESS_BEHAVIOR,
            ctypes.byref(wintypes.DWORD(_INTERNET_SUPPRESS_COOKIE_PERSIST)),
            ctypes.sizeof(wintypes.DWORD),
        )
        logger.debug("WinInet: подавление сохранения кук установлено.")
    except Exception:
        logger.debug("WinInet: не удалось подавить сохранение кук.")


def _end_browser_session_wininet():
    """Завершает сессию Internet Explorer (сбрасывает сессионные куки).

    После этого вызова все сессионные куки IE-бэкенда очищаются.
    """
    try:
        ctypes.windll.wininet.InternetSetOptionW(
            None, _INTERNET_OPTION_END_BROWSER_SESSION, None, 0
        )
        logger.debug("WinInet: сессия браузера завершена.")
    except Exception:
        logger.debug("WinInet: не удалось завершить сессию браузера.")


def _clear_webview_session(browser: wx.html2.WebView) -> None:
    """Сбрасывает историю и куки WebView.

    Изоляция сессий обеспечивается через _IsolatedCookieEnv (WebView2),
    поэтому JS-манипуляции со storage не используются — они вызывают
    предупреждение "Error running JavaScript" на about:blank.
    """
    try:
        browser.ClearHistory()
    except Exception:
        logger.debug("ClearHistory недоступен на этой версии wxPython.")

    try:
        browser.DeleteAllCookies()
    except Exception:
        logger.debug("DeleteAllCookies недоступен на этой версии wxPython.")


class _IsolatedCookieEnv:
    """Создаёт временное окружение для кук WebView.

    Каждый экземпляр класса создаёт уникальную временную папку и
    устанавливает WEBVIEW2_USER_DATA_FOLDER, чтобы следующий созданный
    WebView использовал изолированное хранилище кук.
    После закрытия диалога временная папка удаляется.
    """

    def __init__(self):
        self._temp_dir = os.path.join(
            tempfile.gettempdir(),
            f"ymusic_auth_{uuid.uuid4().hex}",
        )
        os.makedirs(self._temp_dir, exist_ok=True)
        self._old_env = os.environ.get("WEBVIEW2_USER_DATA_FOLDER")
        os.environ["WEBVIEW2_USER_DATA_FOLDER"] = self._temp_dir
        _suppress_cookie_persistence_wininet()
        _end_browser_session_wininet()
        logger.debug("Изолированное хранилище кук: %s", self._temp_dir)

    def cleanup(self):
        """Удаляет временную папку и восстанавливает окружение."""
        os.environ.pop("WEBVIEW2_USER_DATA_FOLDER", None)
        if self._old_env is not None:
            os.environ["WEBVIEW2_USER_DATA_FOLDER"] = self._old_env
        try:
            import shutil
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            logger.debug("Временное хранилище кук удалено: %s", self._temp_dir)
        except Exception:
            logger.debug("Не удалось удалить временное хранилище кук.")


class _AuthBaseDialog(wx.Dialog):
    """Базовый класс для диалогов авторизации с подтверждением закрытия."""

    def __init__(self, parent, title, force_confirm=True, **kwargs):
        style = wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.TAB_TRAVERSAL
        super().__init__(parent, title=title, size=(800, 600), style=style)
        self.token = None
        self._cookie_env = _IsolatedCookieEnv()
        self.Bind(wx.EVT_CLOSE, self._on_close)

    def _confirm_close(self):
        """Запрашивает подтверждение закрытия диалога."""
        if self.token:
            return True
        dlg = wx.MessageDialog(
            self,
            "Вы действительно хотите прервать процесс авторизации?",
            "Подтверждение",
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION,
        )
        result = dlg.ShowModal() == wx.ID_YES
        dlg.Destroy()
        return result

    def _on_close(self, event):
        if self._confirm_close():
            self._cookie_env.cleanup()
            event.Skip()
        else:
            event.Veto()

    def Destroy(self):
        self._cookie_env.cleanup()
        super().Destroy()


class BrowserAuthDialog(_AuthBaseDialog):
    """Классическая веб-авторизация через перехват OAuth-токена в WebView.

    Яндекс перенаправляет браузер на ``https://oauth.yandex.ru/authorize``.
    После успешного входа пользователя Яндекс редиректит на URL с фрагментом
    ``access_token=...``. Dialog перехватывает этот переход и сохраняет токен.

    Для изоляции сессий каждая авторизация использует отдельную временную
    папку для кук WebView2 (через WEBVIEW2_USER_DATA_FOLDER).
    """

    def __init__(self, parent, force_confirm=True):
        super().__init__(parent, title="Авторизация Яндекс (Браузер)", force_confirm=force_confirm)

        auth_url = (
            "https://oauth.yandex.ru/authorize"
            "?response_type=token"
            "&client_id=23cabbbdc6cd418abb4b39c32c41195d"
        )
        if force_confirm:
            auth_url += "&force_confirm=true"

        sizer = wx.BoxSizer(wx.VERTICAL)

        try:
            self.browser = wx.html2.WebView.New(self)
            self.browser.Bind(wx.html2.EVT_WEBVIEW_NAVIGATING, self.on_navigating)
            _clear_webview_session(self.browser)
            self.browser.LoadURL(auth_url)
            sizer.Add(self.browser, 1, wx.EXPAND)
            logger.debug("BrowserAuthDialog: WebView инициализирован.")
        except Exception:
            logger.exception("Не удалось инициализировать WebView в BrowserAuthDialog.")
            label = wx.StaticText(self, label="Ошибка инициализации встроенного браузера.")
            sizer.Add(label, 1, wx.ALIGN_CENTER | wx.ALL, 20)

        self.SetSizer(sizer)
        self.Centre()

    def on_navigating(self, event):
        """Отлавливает редирект Яндекса с access_token во фрагменте URL."""
        url = event.GetURL()
        logger.debug("Переход WebView (BrowserAuth): %s", url)

        if "access_token=" in url:
            try:
                fragment = urllib.parse.urlparse(url).fragment
                params = urllib.parse.parse_qs(fragment)
                if "access_token" in params:
                    self.token = params["access_token"][0]
                    logger.info("Токен успешно перехвачен из BrowserAuthDialog.")
                    self.EndModal(wx.ID_OK)
            except Exception:
                logger.exception("Ошибка при парсинге токена в BrowserAuthDialog.")


class DeviceAuthDialog(_AuthBaseDialog):
    """Авторизация через привязку устройства (passport.yandex.ru/auth).

    Пользователь входит в Яндекс через WebView, после чего Яндекс
    редиректит на yandex.ru/device с access_token во фрагменте URL.
    Dialog перехватывает этот переход и сохраняет токен.

    Для изоляции сессий каждая авторизация использует отдельную временную
    папку для кук WebView2 (через WEBVIEW2_USER_DATA_FOLDER).
    """

    def __init__(self, parent, api=None):
        super().__init__(parent, title="Авторизация устройства")
        self.api = api

        device_url = (
            "https://passport.yandex.ru/auth"
            "?mode=add-user"
            "&force_confirm=true"
            "&retpath=https%3A%2F%2Fyandex.ru%2Fdevice"
        )

        sizer = wx.BoxSizer(wx.VERTICAL)

        try:
            self.browser = wx.html2.WebView.New(self)
            self.browser.Bind(wx.html2.EVT_WEBVIEW_NAVIGATING, self.on_navigating)
            _clear_webview_session(self.browser)
            self.browser.LoadURL(device_url)
            sizer.Add(self.browser, 1, wx.EXPAND)
            logger.debug("DeviceAuthDialog: WebView инициализирован.")
        except Exception:
            logger.exception("Не удалось инициализировать WebView в DeviceAuthDialog.")
            label = wx.StaticText(self, label="Ошибка инициализации встроенного браузера.")
            sizer.Add(label, 1, wx.ALIGN_CENTER | wx.ALL, 20)

        self.SetSizer(sizer)
        self.Centre()

    def on_navigating(self, event):
        """Отлавливает редирект Яндекса с access_token во фрагменте URL."""
        url = event.GetURL()
        logger.debug("Переход WebView (DeviceAuth): %s", url)

        if "access_token=" in url:
            try:
                fragment = urllib.parse.urlparse(url).fragment
                params = urllib.parse.parse_qs(fragment)
                if "access_token" in params:
                    self.token = params["access_token"][0]
                    logger.info("Токен успешно перехвачен из DeviceAuthDialog.")
                    self.EndModal(wx.ID_OK)
            except Exception:
                logger.exception("Ошибка при парсинге токена в DeviceAuthDialog.")
