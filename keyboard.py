# Year: 2026
import wx
import logging
from utils import classify_item

logger = logging.getLogger(__name__)

def handle_key_event(frame, event):
    """Обрабатывает горячие клавиши главного окна."""
    try:
        keycode = event.GetKeyCode()
        ctrl_down = event.ControlDown()
        shift_down = event.ShiftDown()
        alt_down = event.AltDown()

        # Прерывание речи при нажатии клавиши Ctrl
        if keycode == wx.WXK_CONTROL:
            frame.safe_speak("", interrupt=True)
            event.Skip()
            return True

        if keycode == wx.WXK_MENU:
            focus = frame.FindFocus()
            if focus == frame.track_list:
                sel = frame.track_list.GetFirstSelected()
                if sel < 0 and frame.track_list.GetItemCount() > 0:
                    frame.focus_item(0)
                    sel = 0
                if sel >= 0:
                    frame.on_context_menu(None)
            return True

        if keycode == wx.WXK_F1:
            if ctrl_down:
                frame.on_open_license(None)
            elif alt_down:
                frame.on_open_keyboard(None)
            else:
                frame.on_open_help(None)
            return True

        if ctrl_down and keycode == ord('J'):
            frame.toggle_shuffle()
            return True

        if ctrl_down and keycode == ord('K'):
            frame.speak_queue_info()
            return True

        if shift_down and not ctrl_down and not alt_down and keycode == ord('R'):
            if hasattr(frame, 'reset_speed'):
                frame.reset_speed()
            return True

        if shift_down and not ctrl_down and not alt_down and keycode == ord('S'):
            if hasattr(frame, 'speak_download_progress'):
                frame.speak_download_progress()
            return True

        if ctrl_down and keycode == ord('M'):
            frame.on_open_accounts(None)
            return True

        if ctrl_down and keycode == ord('S'):
            focus = frame.FindFocus()
            if focus == frame.track_list:
                sel = frame.track_list.GetFirstSelected()
                if sel >= 0:
                    item = frame.track_list.data[sel]
                    obj_type = classify_item(item)
                    if obj_type == 'track':
                        frame.start_download(item)
                    elif obj_type == 'playlist':
                        frame.download_playlist_zip(item)
                    elif obj_type == 'album':
                        frame.download_album_zip(item)
            return True

        if ctrl_down and keycode == ord('O'):
            focus = frame.FindFocus()
            if focus == frame.track_list:
                sel = frame.track_list.GetFirstSelected()
                if sel >= 0:
                    item = frame.track_list.data[sel]
                    if classify_item(item) == 'track':
                        frame.open_in_system_player(item)
            return True

        if ctrl_down and keycode == ord('L'):
            focus = frame.FindFocus()
            if focus == frame.track_list:
                sel = frame.track_list.GetFirstSelected()
                if sel >= 0:
                    item = frame.track_list.data[sel]
                    obj_type = classify_item(item)
                    if obj_type == 'playlist':
                        frame.handle_like(item, 'playlist', True)
                    elif obj_type == 'album':
                        frame.handle_like(item.id, 'album', True)
                    else:
                        frame.handle_like(item.id, 'track', True)
            return True

        if shift_down and not ctrl_down and not alt_down and keycode == ord('L'):
            focus = frame.FindFocus()
            if focus == frame.track_list:
                sel = frame.track_list.GetFirstSelected()
                if sel >= 0:
                    item = frame.track_list.data[sel]
                    obj_type = classify_item(item)
                    if obj_type == 'playlist':
                        frame.handle_like(item, 'playlist', False)
                    elif obj_type == 'album':
                        frame.handle_like(item.id, 'album', False)
                    else:
                        frame.handle_like(item.id, 'track', False)
            return True

        if ctrl_down and keycode == ord('C'):
            focus = frame.FindFocus()
            if focus == frame.track_list:
                sel = frame.track_list.GetFirstSelected()
                if sel >= 0:
                    frame.copy_link(frame.track_list.data[sel])
            return True

        if ctrl_down and keycode == ord('P'):
            frame.on_open_settings(None)
            return True
            

        if ctrl_down and keycode == ord('D'):
            frame.show_devices_menu()
            return True

        if ctrl_down and keycode == ord('U'):
            frame.show_eq_menu()
            return True

        if ctrl_down and shift_down and keycode == ord('T'):
            frame.speak_remaining_time()
            return True

        if ctrl_down and keycode == ord('T'):
            frame.speak_total_duration()
            return True

        if shift_down and not ctrl_down and not alt_down and keycode == ord('T'):
            frame.speak_elapsed_time()
            return True

        if keycode == wx.WXK_ESCAPE:
            frame.go_back()
            return True

        if keycode == wx.WXK_RETURN:
            focus = frame.FindFocus()
            if focus == frame.track_list:
                frame.on_item_activated(None)
                return True
            elif focus == frame.search_input:
                frame.on_search(None)
                return True

        if keycode == wx.WXK_SPACE:
            if frame.FindFocus() == frame.track_list:
                frame.on_show_item_info(None)
                return True

        if handle_media_key_event(frame, event, allow_space=False):
            return True

    except Exception as ex:
        logger.error(f"Ошибка в хуке клавиатуры: {ex}", exc_info=True)

    return False


def handle_media_key_event(frame, event, allow_space=False):
    """Обрабатывает клавиши управления воспроизведением.

    Используется и в главном окне, и в диалогах (плейлисты и т.п.), чтобы
    горячие клавиши воспроизведения работали везде, где бы ни находился фокус.
    allow_space=True разрешает паузу по пробелу (в диалогах), в главном окне
    пробел обрабатывается отдельно и только при фокусе на списке.
    """
    try:
        keycode = event.GetKeyCode()
        ctrl_down = event.ControlDown()
        shift_down = event.ShiftDown()
        alt_down = event.AltDown()

        if keycode == wx.WXK_SPACE and allow_space:
            focus = wx.Window.FindFocus()
            if not (focus and isinstance(focus, wx.TextCtrl)):
                if isinstance(focus, wx.ListCtrl):
                    frame.player.toggle_pause()
                    state = frame.player.get_state()
                    if state == 3:
                        frame.safe_speak("Пауза", "media")
                    elif state == 1:
                        frame.safe_speak("Воспроизведение", "media")
                    return True
            return False

        if ctrl_down and keycode == ord('K'):
            frame.speak_queue_info()
            return True

        if ctrl_down and keycode == ord('J'):
            frame.toggle_shuffle()
            return True

        if ctrl_down and keycode == ord('R'):
            frame.item_repeat.Check(not frame.item_repeat.IsChecked())
            frame.on_toggle_repeat(None)
            return True

        if shift_down and not ctrl_down and not alt_down and keycode == ord('R'):
            if hasattr(frame, 'reset_speed'):
                frame.reset_speed()
            return True

        if shift_down and not ctrl_down and not alt_down and keycode == ord('S'):
            if hasattr(frame, 'speak_download_progress'):
                frame.speak_download_progress()
            return True

        if keycode == wx.WXK_F7:
            if ctrl_down and not alt_down:
                frame.player.seek(-5.0)
                frame.safe_speak("Перемотка назад 5 секунд", "media")
            elif shift_down and not ctrl_down and not alt_down:
                frame.player.seek(-10.0)
                frame.safe_speak("Перемотка назад 10 секунд", "media")
            else:
                frame.toggle_pause_speak()
            return True

        if keycode == wx.WXK_F8:
            if ctrl_down and not alt_down:
                frame.player.seek(5.0)
                frame.safe_speak("Перемотка вперед 5 секунд", "media")
            elif shift_down and not ctrl_down and not alt_down:
                frame.player.seek(10.0)
                frame.safe_speak("Перемотка вперед 10 секунд", "media")
            else:
                frame.stop_playback()
            return True

        if keycode == wx.WXK_F5:
            if shift_down and not ctrl_down and not alt_down:
                frame.change_speed(-0.05)
            else:
                frame.change_volume(-0.1)
            return True

        if keycode == wx.WXK_F6:
            if shift_down and not ctrl_down and not alt_down:
                frame.change_speed(0.05)
            else:
                frame.change_volume(0.1)
            return True

        if alt_down and not ctrl_down:
            if keycode == wx.WXK_UP:
                frame.play_previous()
                return True
            elif keycode == wx.WXK_DOWN:
                frame.play_next()
                return True

    except Exception as ex:
        logger.error(f"Ошибка в обработке клавиш воспроизведения: {ex}", exc_info=True)

    return False
