"""
Discord通知マネージャー (discordNotifyManager)
Windows 11環境でDiscordの通知を常時監視し、3秒後に自動削除しながらJSONにログ保存する常駐ツール
- タスクトレイ常駐機能（pystray）
- Windowsスタートアップ自動起動機能（winreg）
- 一時停止・再開トグル機能
"""

import asyncio
import datetime
import json
import os
import re
import sys
import threading
import winreg
from typing import Optional

# WindowsコンソールでのUnicode (絵文字や特殊文字) 出力エラー防止 & リアルタイム出力
if sys.platform == "win32" and sys.stdout:
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass

# 画像生成 (Pillow) とタスクトレイ (pystray) のインポート
from PIL import Image, ImageDraw
import pystray

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
# スタートアップ起動時にも正常に動作するよう、スクリプト/exeの絶対配置ディレクトリを基準にする
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 保存先JSONファイル名
LOG_FILE = os.path.join(BASE_DIR, "discord_notifications.json")

# 自動削除までの待機時間（秒）
AUTO_DELETE_DELAY_SECONDS = 3

# 監視ポーリング間隔（秒）
POLLING_INTERVAL_SECONDS = 0.5

# スタートアップ登録用のレジストリキー名
STARTUP_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_APP_NAME = "DiscordNotifyManager"

# グローバル状態管理
is_auto_delete_enabled = True  # 自動削除が有効か（トレイから一時停止可能）
tray_icon: Optional[pystray.Icon] = None
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
                # 実行コマンドの作成
                if getattr(sys, "frozen", False):
                    # PyInstaller 等で .exe 化されている場合
                    cmd = f'"{os.path.abspath(sys.executable)}"'
                else:
                    # スクリプト実行時は、黒いコンソールが出ない pythonw.exe を優先指定
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
    # 64x64ピクセルのRGBA画像を作成
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
    # Discordのチャンネル名等に付与される \u2068, \u2069 などを除去
    cleaned = re.sub(r"[\u2066-\u2069\u202a-\u202e]", "", text)
    return cleaned.strip()


def save_notification_log(entry: dict):
    """通知情報を discord_notifications.json に安全に追記保存する関数"""
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

    # ファイル破損を防止するため一時ファイル経由で保存
    temp_file = f"{LOG_FILE}.tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp_file, LOG_FILE)


async def delete_notification_after_delay(listener: mgmt.UserNotificationListener, notif_id: int, delay: int):
    """指定秒数待機した後に通知を画面および通知センターから削除する非同期タスク"""
    await asyncio.sleep(delay)
    # 一時停止に切り替わっていないか再確認
    if not is_auto_delete_enabled:
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ⏸️ [スキップ] 一時停止中のため通知ID: {notif_id} の削除を保留しました。")
        return

    try:
        listener.remove_notification(notif_id)
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🗑️ [自動削除完了] 通知ID: {notif_id}")
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

    # 1. UserNotificationListener の権限確認
    listener = mgmt.UserNotificationListener.current
    access_status = await listener.request_access_async()

    if access_status != mgmt.UserNotificationListenerAccessStatus.ALLOWED:
        print("\n【エラー】Windowsの通知アクセス権限が拒否されました。")
        print("Windowsの「設定 > プライバシーとセキュリティ > 通知」からアクセスを許可してください。\n")
        return

    print("✅ 通知アクセス権限: 許可されています")

    # 2. 起動時点で既に存在する通知をスキップ対象として登録
    initial_notifications = await listener.get_notifications_async(notifications.NotificationKinds.TOAST)
    seen_ids = {item.id for item in initial_notifications}
    print(f"ℹ️ 現在の既存通知: {len(seen_ids)} 件をスキップ対象として登録しました。")
    print(f"🚀 新着Discord通知の常時監視を開始しました（{AUTO_DELETE_DELAY_SECONDS}秒後に自動削除）...")

    # 3. 監視ループ
    while not stop_event.is_set():
        try:
            current_notifications = await listener.get_notifications_async(notifications.NotificationKinds.TOAST)

            for item in current_notifications:
                notif_id = item.id

                # 処理済みの通知はスキップ
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

                # Discord通知か判定
                if "discord" not in app_name.lower():
                    continue

                # 受信日時の取得
                received_dt = item.creation_time
                if received_dt:
                    local_dt_str = received_dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                else:
                    local_dt_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # タイトルおよび本文テキストの抽出
                texts = []
                if item.notification and item.notification.visual:
                    for binding in item.notification.visual.bindings:
                        for text_element in binding.get_text_elements():
                            texts.append(clean_discord_text(text_element.text))

                title = texts[0] if len(texts) > 0 else "（タイトルなし）"
                body = "\n".join(texts[1:]) if len(texts) > 1 else "（本文なし）"

                # コンソールに表示
                status_label = "🔔 [Discord新着通知検知]" if is_auto_delete_enabled else "🔔 [Discord新着通知検知 (自動削除一時停止中)]"
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {status_label}")
                print(f"  ・ID    : {notif_id}")
                print(f"  ・日時  : {local_dt_str}")
                print(f"  ・送信元: {title}")
                print(f"  ・内容  : {body}")

                # JSONログに保存（一時停止中でもログ保存は行う）
                log_entry = {
                    "id": notif_id,
                    "received_at": local_dt_str,
                    "app_name": app_name,
                    "title": title,
                    "body": body,
                }
                save_notification_log(log_entry)
                print(f"  💾 -> '{LOG_FILE}' に保存しました。")

                # 自動削除タスクを開始（有効時のみ）
                if is_auto_delete_enabled:
                    asyncio.create_task(
                        delete_notification_after_delay(listener, notif_id, AUTO_DELETE_DELAY_SECONDS)
                    )

        except Exception as e:
            print(f"監視ループ内で例外が発生しました: {e}")

        # ポーリング待機
        await asyncio.sleep(POLLING_INTERVAL_SECONDS)


def run_async_loop():
    """ワーカースレッド上で非同期ループを実行する関数"""
    asyncio.run(notification_monitor_loop())


# ==============================================================================
# タスクトレイUI関連 (pystray)
# ==============================================================================
def on_toggle_auto_delete(icon, item):
    """自動削除の有効/一時停止を切り替えるハンドラー"""
    global is_auto_delete_enabled
    is_auto_delete_enabled = not is_auto_delete_enabled

    # アイコンとツールチップの更新
    icon.icon = create_tray_image(is_auto_delete_enabled)
    status_text = "監視中 (3秒後に自動削除)" if is_auto_delete_enabled else "一時停止中 (ログ保存のみ)"
    icon.title = f"Discord通知マネージャー - {status_text}"
    print(f"ℹ️ 自動削除を {'有効' if is_auto_delete_enabled else '一時停止'} に切り替えました。")


def on_toggle_startup(icon, item):
    """スタートアップ登録の切り替えハンドラー"""
    current_state = is_startup_enabled()
    set_startup_enabled(not current_state)


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


def on_exit(icon, item):
    """アプリ終了ハンドラー"""
    print("👋 終了処理を実行しています...")
    stop_event.set()
    icon.stop()


def setup_tray():
    """タスクトレイアイコンとメニューを初期化して起動する関数"""
    global tray_icon

    # メニュー項目の組み立て
    menu = pystray.Menu(
        pystray.MenuItem(
            lambda text: "自動削除: 有効 (クリックで一時停止)" if is_auto_delete_enabled else "自動削除: 一時停止中 (クリックで再開)",
            on_toggle_auto_delete,
        ),
        pystray.Menu.SEPARATOR,
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

    initial_image = create_tray_image(True)
    tray_icon = pystray.Icon(
        "DiscordNotifyManager",
        initial_image,
        "Discord通知マネージャー - 監視中 (3秒後に自動削除)",
        menu=menu,
    )

    # バックグラウンドスレッドで非同期通知監視を開始
    monitor_thread = threading.Thread(target=run_async_loop, daemon=True)
    monitor_thread.start()

    # メインスレッドでタスクトレイのイベントループを開始（ブロッキング）
    try:
        tray_icon.run()
    except KeyboardInterrupt:
        print("\n👋 Ctrl+C により終了しました。")
        stop_event.set()
        tray_icon.stop()


# ==============================================================================
# エントリーポイント
# ==============================================================================
if __name__ == "__main__":
    setup_tray()
