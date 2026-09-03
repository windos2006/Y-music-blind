# Year: 2026
import wx
import wx.adv
import logging

logger = logging.getLogger(__name__)


class AppTaskBarIcon(wx.adv.TaskBarIcon):
    """Иконка в системном трее с контекстным меню управления плеером."""

    def __init__(self, frame):
        super().__init__()
        self.frame = frame
        self._icon = wx.Icon(wx.ArtProvider.GetBitmap(
            wx.ART_INFORMATION, wx.ART_OTHER, (16, 16),
        ))
        self.SetIcon(self._icon, "y-music-blind")

        self.Bind(wx.adv.EVT_TASKBAR_LEFT_DCLICK, self.on_left_dblclick)
        self.Bind(wx.adv.EVT_TASKBAR_RIGHT_UP, self.on_right_click)

    def on_left_dblclick(self, event):
        """Двойной щелчок — показать главное окно."""
        self._show_frame()

    def on_right_click(self, event):
        """Контекстное меню иконки."""
        menu = wx.Menu()

        item_show = menu.Append(wx.ID_ANY, "Показать окно")
        menu.AppendSeparator()

        item_prev = menu.Append(wx.ID_ANY, "Предыдущий трек")
        is_playing = self.frame.player.get_state() == 1
        is_paused = self.frame.player.get_state() == 3
        if is_playing or is_paused:
            label = "Пауза" if is_playing else "Воспроизведение"
        else:
            label = "Воспроизведение"
        item_play = menu.Append(wx.ID_ANY, label)
        item_next = menu.Append(wx.ID_ANY, "Следующий трек")
        menu.AppendSeparator()
        item_quit = menu.Append(wx.ID_ANY, "Выход")

        def _do_exit():
            try:
                self.RemoveIcon()
                self.Destroy()
            except Exception:
                pass
            wx.Exit()

        menu.Bind(wx.EVT_MENU, lambda e: self._show_frame(), item_show)
        menu.Bind(wx.EVT_MENU, lambda e: wx.CallAfter(self.frame.play_previous), item_prev)
        menu.Bind(wx.EVT_MENU, lambda e: wx.CallAfter(self.frame.toggle_pause_speak), item_play)
        menu.Bind(wx.EVT_MENU, lambda e: wx.CallAfter(self.frame.play_next), item_next)
        menu.Bind(wx.EVT_MENU, lambda e: wx.CallAfter(_do_exit), item_quit)

        self.PopupMenu(menu)
        menu.Destroy()

    def _show_frame(self):
        """Показывает главное окно, восстанавливает фокус и активизирует его."""
        if self.frame.IsIconized():
            self.frame.Restore()
        if not self.frame.IsShown():
            self.frame.Show()
        self.frame.Raise()
        # Восстанавливаем фокус на последний активный элемент или на трек-лист
        if hasattr(self.frame, '_last_focused_window') and self.frame._last_focused_window:
            try:
                self.frame._last_focused_window.SetFocus()
                return
            except Exception:
                pass
        if hasattr(self.frame, 'track_list') and self.frame.track_list:
            self.frame.track_list.SetFocus()
        else:
            self.frame.SetFocus()

    def update_tooltip(self, track_title=""):
        """Обновляет текст подсказки (название текущего трека)."""
        tooltip = "y-music-blind"
        if track_title:
            tooltip = f"{track_title} — y-music-blind"
        self.SetIcon(self._icon, tooltip)
