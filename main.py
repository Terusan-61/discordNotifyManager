"""
Discord通知マネージャー (discordNotifyManager)
Windows 11環境でDiscordの通知を監視し、3秒後に自動削除しながらJSONにログ保存するスクリプト
"""

import asyncio
import datetime
import json
import os
import re
import sys

# WindowsコンソールでのUnicode (絵文字や特殊文字) 出力エラー防止 & リアルタイム出力
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

# WinRT API のインポート
try:
    import winrt.windows.ui.notifications as notifications
    import winrt.windows.ui.notifications.management as mgmt
except ImportError as e:
    print(f"【エラー】必要なWinRTライブラリがインストールされていません: {e}")
    print("pip install -r requirements.txt を実行してください。")
    sys.exit(1)

# 保存先JSONファイル名
LOG_FILE = "discord_notifications.json"

# 自動削除までの待機時間（秒）
AUTO_DELETE_DELAY_SECONDS = 3

# 監視ポーリング間隔（秒）
POLLING_INTERVAL_SECONDS = 0.5


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
            print(f"【警告】既存のログファイル読み込みに失敗したため新規作成します: {e}")
            data = []

    data.append(entry)

    # 破損防止のため一時ファイルに書き込んでから置換
    temp_file = f"{LOG_FILE}.tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp_file, LOG_FILE)


async def delete_notification_after_delay(listener: mgmt.UserNotificationListener, notif_id: int, delay: int):
    """指定秒数待機した後に通知を画面および通知センターから削除する非同期タスク"""
    await asyncio.sleep(delay)
    try:
        listener.remove_notification(notif_id)
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🗑️ [自動削除完了] 通知ID: {notif_id}")
    except Exception as e:
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ⚠️ [削除失敗] 通知ID: {notif_id} (理由: {e})")


async def main():
    print("========================================")
    print("  Discord 通知マネージャー 起動中...")
    print("========================================")

    # 1. UserNotificationListener のインスタンス取得と権限確認
    listener = mgmt.UserNotificationListener.current

    print("Windowsの通知アクセス権限を確認しています...")
    access_status = await listener.request_access_async()

    if access_status != mgmt.UserNotificationListenerAccessStatus.ALLOWED:
        print("\n【エラー】Windowsの通知アクセス権限が拒否されました。")
        print("Windowsの「設定 > プライバシーとセキュリティ > 通知」から")
        print("アプリによる通知へのアクセスを許可してください。\n")
        sys.exit(1)

    print("✅ 通知アクセス権限: 許可されています")

    # 2. 起動時点で既に存在する通知を把握し、過去の通知を誤削除しないよう登録
    initial_notifications = await listener.get_notifications_async(notifications.NotificationKinds.TOAST)
    seen_ids = {item.id for item in initial_notifications}
    print(f"ℹ️ 現在の既存通知: {len(seen_ids)} 件をスキップ対象として登録しました。")
    print(f"🚀 新着Discord通知の監視を開始しました（{AUTO_DELETE_DELAY_SECONDS}秒後に自動削除）...")
    print("※ 終了するには Ctrl+C を押してください。\n")

    # 3. 通知監視ループ
    try:
        while True:
            # 現在のトースト通知一覧を取得
            current_notifications = await listener.get_notifications_async(notifications.NotificationKinds.TOAST)

            for item in current_notifications:
                notif_id = item.id

                # すでに処理済みの通知はスキップ
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

                # Discordの通知か判定
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

                # コンソールに通知受信ログを表示
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🔔 [Discord新着通知検知]")
                print(f"  ・ID    : {notif_id}")
                print(f"  ・日時  : {local_dt_str}")
                print(f"  ・送信元: {title}")
                print(f"  ・内容  : {body}")

                # 4. JSONに追記保存
                log_entry = {
                    "id": notif_id,
                    "received_at": local_dt_str,
                    "app_name": app_name,
                    "title": title,
                    "body": body,
                }
                save_notification_log(log_entry)
                print(f"  💾 -> '{LOG_FILE}' に保存しました。")

                # 5. 後続処理をブロックせずに3秒後に削除する非同期タスクを開始
                asyncio.create_task(
                    delete_notification_after_delay(listener, notif_id, AUTO_DELETE_DELAY_SECONDS)
                )

            # 指定間隔待機
            await asyncio.sleep(POLLING_INTERVAL_SECONDS)

    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n👋 監視スクリプトを終了しました。")


if __name__ == "__main__":
    asyncio.run(main())
