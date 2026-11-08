"""
Discord通知マネージャー (discordNotifyManager)
Windows 11環境でDiscordの通知を常時監視し、指定秒数後に自動削除しながらJSONにログ保存する常駐ツール
- タスクトレイ常駐機能（pystray）
- モダンな管理画面GUI（CustomTkinter: ui.py）
- 設定の一元管理（config.json: config.py）
- Windowsスタートアップ自動起動機能（winreg）
"""

import asyncio
import datetime
import json
import os
import re
import sys
import threading
import winreg
from typing import Any, Dict, Optional

# WindowsコンソールでのUnicode (絵文字や特殊文字) 出力エラー防止 & リアルタイム出力
if sys.platform == "win32" and sys.stdout:
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass

# 画像生成 (Pillow) とタスクトレイ (pystray) のインポート
from PIL import Image, ImageDraw
import pystray

# 設定モジュール & GUIモジュールのインポート
import config
import ui

# WinRT API のインポート
try:
    import winrt.windows.ui.notifications as notifications
    import winrt.windows.ui.notifications.management as mgmt
except ImportError as e:
    print(f"【エラー】必要なWinRTライブラリがインストールされていません: {e}")
    print("pip install -r requirements.txt を実行してください。")
    sys.exit(1)

# ==============================================================================
# 基本設定・パス解決
# ==============================================================================
BASE_DIR = config.BASE_DIR
LOG_FILE = os.path.join(BASE_DIR, "discord_notifications.json")

# 監視ポーリング間隔（秒）
POLLING_INTERVAL_SECONDS = 0.5

# スタートアップ登録用のレジストリキー名
STARTUP_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_APP_NAME = "DiscordNotifyManager"

# グローバルインスタンス
tray_icon: Optional[pystray.Icon] = None
ui_app: Optional[ui.DiscordNotifyManagerUI] = None
stop_event = threading.Event()  # アプリ全体の終了シグナル


# ==============================================================================
# スタートアップ登録関連 (winreg)
# ==============================================================================
def is_startup_enabled() -> bool:
    """Windowsのスタートアップに登録されているか確認する関数"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, STARTUP_APP_NAME)
            return True
    except FileNotFoundError:
        return False
    except Exception as e:
        print(f"スタートアップ確認エラー: {e}")
        return False


def set_startup_enabled(enable: bool) -> bool:
    """Windowsのスタートアップ登録を有効化/無効化する関数"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                if getattr(sys, "frozen", False):
                    cmd = f'"{os.path.abspath(sys.executable)}"'
                else:
                    python_exe = sys.executable
                    pythonw_exe = os.path.join(os.path.dirname(python_exe), "pythonw.exe")
                    if not os.path.exists(pythonw_exe):
                        pythonw_exe = python_exe
                    script_path = os.path.abspath(__file__)
                    cmd = f'"{pythonw_exe}" "{script_path}"'

                winreg.SetValueEx(key, STARTUP_APP_NAME, 0, winreg.REG_SZ, cmd)
                print("✅ スタートアップに登録しました。")
            else:
                try:
                    winreg.DeleteValue(key, STARTUP_APP_NAME)
                    print("🗑️ スタートアップ登録を解除しました。")
                except FileNotFoundError:
                    pass
        return True
    except Exception as e:
        print(f"スタートアップ設定の変更に失敗しました: {e}")
        return False


# ==============================================================================
# アイコン生成 (Pillow)
# ==============================================================================
def create_tray_image(enabled: bool = True) -> Image.Image:
    """タスクトレイ用のアイコン画像を動的生成する関数（Discordカラー＋吹き出し＋稼働状態ドット）"""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 背景の円 (稼働時はDiscord Blurple色、一時停止時はグレー)
    bg_color = (88, 101, 242, 255) if enabled else (120, 120, 120, 255)
    draw.ellipse((4, 4, 60, 60), fill=bg_color)

    # 吹き出し（白）
    draw.rounded_rectangle((16, 16, 48, 42), radius=6, fill=(255, 255, 255, 255))
    draw.polygon([(22, 42), (32, 42), (22, 52)], fill=(255, 255, 255, 255))

    # 右下の稼働状態インジケーター（有効時は緑、一時停止時は赤）
    dot_color = (87, 242, 135, 255) if enabled else (237, 66, 69, 255)
    draw.ellipse((42, 42, 58, 58), fill=dot_color)

    return img


# ==============================================================================
# 通知ログ保存・クレンジング処理
# ==============================================================================
def clean_discord_text(text: str) -> str:
    """Discord通知に含まれるUnicode方向制御文字などを除去して綺麗にする関数"""
    if not text:
        return ""
    cleaned = re.sub(r"[\u2066-\u2069\u202a-\u202e]", "", text)
    return cleaned.strip()


def save_notification_log(entry: dict):
    """通知情報を discord_notifications.json に安全に追記保存する関数（最大件数制限付き）"""
    cfg = config.get_config()
    max_count = cfg.get("max_log_count", 500)
    pinned_ids = set(cfg.get("pinned_notification_ids", []))

    data = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, list):
                    data = []
        except Exception as e:
            print(f"【警告】既存ログの読み込み失敗のため新規作成します: {e}")
            data = []

    data.append(entry)

    # 最大件数を超えている場合、古い順に消去（ただしピン留めされている通知は保護）
    if len(data) > max_count:
        excess = len(data) - max_count
        new_data = []
        removed_count = 0
        for item in data:
            if removed_count < excess and item.get("id") not in pinned_ids:
                removed_count += 1
                continue
            new_data.append(item)
        data = new_data

    # 一時ファイル経由でアトミック保存
    temp_file = f"{LOG_FILE}.tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp_file, LOG_FILE)

    # UIが開いていればリアルタイム反映
    if ui_app:
        try:
            ui_app.after(0, ui_app.refresh_logs)
        except Exception:
            pass


async def delete_notification_after_delay(listener: mgmt.UserNotificationListener, notif_id: int, delay: int):
    """指定秒数待機した後に通知を画面および通知センターから削除する非同期タスク"""
    await asyncio.sleep(delay)

    # 設定で自動削除が無効化されていないか確認
    cfg = config.get_config()
    if not cfg.get("auto_delete_enabled", True):
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ⏸️ [スキップ] 自動削除一時停止中のため通知ID: {notif_id} の削除を保留しました。")
        return

    try:
        listener.remove_notification(notif_id)
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🗑️ [自動削除完了] 通知ID: {notif_id}（{delay}秒後）")
    except Exception as e:
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ⚠️ [削除失敗] 通知ID: {notif_id} (理由: {e})")


# ==============================================================================
# 通知監視ループ (asyncio)
# ==============================================================================
async def notification_monitor_loop():
    """通知監視を行うメイン非同期ルーチン"""
    print("========================================")
    print("  Discord 通知マネージャー 起動中...")
    print("========================================")

    # 1. 権限確認
    listener = mgmt.UserNotificationListener.current
    access_status = await listener.request_access_async()

    if access_status != mgmt.UserNotificationListenerAccessStatus.ALLOWED:
        print("\n【エラー】Windowsの通知アクセス権限が拒否されました。")
        print("Windowsの「設定 > プライバシーとセキュリティ > 通知」からアクセスを許可してください。\n")
        return

    print("✅ 通知アクセス権限: 許可されています")

    # 2. 既存通知をスキップ対象として登録
    initial_notifications = await listener.get_notifications_async(notifications.NotificationKinds.TOAST)
    seen_ids = {item.id for item in initial_notifications}
    print(f"ℹ️ 現在の既存通知: {len(seen_ids)} 件をスキップ対象として登録しました。")

    cfg = config.get_config()
    delay_sec = cfg.get("auto_delete_delay_seconds", 3)
    save_log = cfg.get("save_log_enabled", True)
    print(f"🚀 新着Discord通知の常時監視を開始しました（{delay_sec}秒後に自動削除 / ログ保存: {'ON' if save_log else 'OFF'}）...")

    # 3. 監視ループ
    while not stop_event.is_set():
        try:
            current_notifications = await listener.get_notifications_async(notifications.NotificationKinds.TOAST)

            for item in current_notifications:
                notif_id = item.id

                if notif_id in seen_ids:
                    continue

                seen_ids.add(notif_id)

                # アプリ名の判定
                app_name = ""
                if item.app_info:
                    if hasattr(item.app_info, "display_info") and item.app_info.display_info:
                        app_name = item.app_info.display_info.display_name or ""
                    if not app_name and hasattr(item.app_info, "id"):
                        app_name = item.app_info.id or ""

                if "discord" not in app_name.lower():
                    continue

                # 受信日時
                received_dt = item.creation_time
                if received_dt:
                    local_dt_str = received_dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                else:
                    local_dt_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # タイトル・本文の抽出
                texts = []
                if item.notification and item.notification.visual:
                    for binding in item.notification.visual.bindings:
                        for text_element in binding.get_text_elements():
                            texts.append(clean_discord_text(text_element.text))

                title = texts[0] if len(texts) > 0 else "（タイトルなし）"
                body = "\n".join(texts[1:]) if len(texts) > 1 else "（本文なし）"

                # 現在の設定を再取得
                current_cfg = config.get_config()
                is_enabled = current_cfg.get("auto_delete_enabled", True)
                current_delay = current_cfg.get("auto_delete_delay_seconds", 3)
                is_save_log_enabled = current_cfg.get("save_log_enabled", True)

                status_label = "🔔 [Discord新着通知検知]" if is_enabled else "🔔 [Discord新着通知検知 (自動削除一時停止中)]"
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {status_label}")
                print(f"  ・ID    : {notif_id}")
                print(f"  ・日時  : {local_dt_str}")
                print(f"  ・送信元: {title}")
                print(f"  ・内容  : {body}")

                # JSONログに保存（設定で有効な場合のみ）
                if is_save_log_enabled:
                    log_entry = {
                        "id": notif_id,
                        "received_at": local_dt_str,
                        "app_name": app_name,
                        "title": title,
                        "body": body,
                    }
                    save_notification_log(log_entry)
                    print(f"  💾 -> '{LOG_FILE}' に保存しました。")
                else:
                    print("  📝 -> [スキップ] ログ保存設定がオフのためファイル保存を省略しました。")

                # 自動削除タスクを開始
                if is_enabled:
                    asyncio.create_task(
                        delete_notification_after_delay(listener, notif_id, current_delay)
                    )

        except Exception as e:
            print(f"監視ループ内で例外が発生しました: {e}")

        await asyncio.sleep(POLLING_INTERVAL_SECONDS)


def run_async_loop():
    """ワーカースレッド上で非同期ループを実行する関数"""
    asyncio.run(notification_monitor_loop())


# ==============================================================================
# タスクトレイUI関連 (pystray)
# ==============================================================================
def update_tray_state(enabled: bool):
    """トレイアイコンの見た目とツールチップを更新"""
    if not tray_icon:
        return
    tray_icon.icon = create_tray_image(enabled)
    delay_sec = config.get_config().get("auto_delete_delay_seconds", 3)
    status_text = f"監視中 ({delay_sec}秒後に自動削除)" if enabled else "一時停止中"
    tray_icon.title = f"Discord通知マネージャー - {status_text}"


def on_toggle_auto_delete(icon, item):
    """トレイメニューから自動削除の有効/一時停止を切り替えるハンドラー"""
    cfg = config.get_config()
    new_state = not cfg.get("auto_delete_enabled", True)
    config.update_config_value("auto_delete_enabled", new_state)
    update_tray_state(new_state)

    if ui_app:
        ui_app.after(0, ui_app._load_settings_into_form)

    print(f"ℹ️ 自動削除を {'有効' if new_state else '一時停止'} に切り替えました。")


def on_open_gui(icon, item):
    """管理画面GUI（CustomTkinter）を開くハンドラー"""
    if ui_app:
        ui_app.after(0, ui_app.show_window)


def on_open_log(icon, item):
    """通知ログファイル (JSON) を開くハンドラー"""
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write("[]\n")
    try:
        os.startfile(LOG_FILE)
    except Exception as e:
        print(f"ログファイルを開けませんでした: {e}")


def on_open_folder(icon, item):
    """保存先フォルダをエクスプローラーで開くハンドラー"""
    try:
        os.startfile(BASE_DIR)
    except Exception as e:
        print(f"フォルダを開けませんでした: {e}")


def on_toggle_startup(icon, item):
    """スタートアップ登録の切り替えハンドラー"""
    current_state = is_startup_enabled()
    set_startup_enabled(not current_state)


def on_exit(icon, item):
    """アプリ終了ハンドラー"""
    print("👋 終了処理を実行しています...")
    stop_event.set()
    if tray_icon:
        tray_icon.stop()
    if ui_app:
        ui_app.after(0, ui_app.destroy)


def on_config_changed_from_ui(new_config: Dict[str, Any]):
    """UI側で設定が保存された際の通知ハンドラー"""
    enabled = new_config.get("auto_delete_enabled", True)
    update_tray_state(enabled)


def setup_tray_and_app():
    """タスクトレイとCustomTkinter GUIを初期化してメインループを開始"""
    global tray_icon, ui_app

    # 設定ファイルのロード
    cfg = config.load_config()
    is_enabled = cfg.get("auto_delete_enabled", True)

    # 1. CustomTkinter UI のインスタンス化（メインスレッドで実行）
    ui_app = ui.DiscordNotifyManagerUI(LOG_FILE, on_config_changed=on_config_changed_from_ui)
    ui_app.withdraw()  # 初期状態はタスクトレイ常駐のため非表示

    # 2. タスクトレイアイコンとメニューの定義
    menu = pystray.Menu(
        pystray.MenuItem("🖥️ 管理画面（ログ・設定）を開く", on_open_gui, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            lambda text: "自動削除: 有効 (クリックで一時停止)" if config.get_config().get("auto_delete_enabled", True) else "自動削除: 一時停止中 (クリックで再開)",
            on_toggle_auto_delete,
        ),
        pystray.MenuItem("通知ログ (JSON) を開く", on_open_log),
        pystray.MenuItem("保存先フォルダを開く", on_open_folder),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Windows起動時に自動実行",
            on_toggle_startup,
            checked=lambda item: is_startup_enabled(),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("終了", on_exit),
    )

    initial_image = create_tray_image(is_enabled)
    delay_sec = cfg.get("auto_delete_delay_seconds", 3)
    status_text = f"監視中 ({delay_sec}秒後に自動削除)" if is_enabled else "一時停止中"
    tray_icon = pystray.Icon(
        "DiscordNotifyManager",
        initial_image,
        f"Discord通知マネージャー - {status_text}",
        menu=menu,
    )

    # 3. バックグラウンドスレッドで通知監視ループを開始
    monitor_thread = threading.Thread(target=run_async_loop, daemon=True)
    monitor_thread.start()

    # 4. タスクトレイアイコンをデタッチ（別スレッド）で起動
    tray_icon.run_detached()

    # 5. メインスレッドで CustomTkinter の mainloop を実行
    try:
        ui_app.mainloop()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        stop_event.set()
        if tray_icon:
            tray_icon.stop()


# ==============================================================================
# エントリーポイント
# ==============================================================================
if __name__ == "__main__":
    setup_tray_and_app()
