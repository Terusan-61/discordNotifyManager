"""
設定管理モジュール (config.py)
config.json の読み込み・保存・デフォルト値管理を担当します。
"""

import json
import os
import sys
from typing import Any, Dict

# スクリプト/exeの配置ディレクトリ
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# デフォルト設定値
DEFAULT_CONFIG: Dict[str, Any] = {
    "auto_delete_delay_seconds": 3,      # 自動削除までの待機時間（秒）
    "max_log_count": 500,                # ログの最大保持件数（上限を超えると最も古いものを自動削除）
    "auto_delete_enabled": True,         # 自動削除機能が有効かどうか
    "pinned_notification_ids": [],       # ピン留め（あとで読む）された通知IDリスト
}

_current_config: Dict[str, Any] = {}


def load_config() -> Dict[str, Any]:
    """設定ファイルを読み込みます。ファイルがない場合はデフォルト設定を作成します。"""
    global _current_config
    config = DEFAULT_CONFIG.copy()

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    # 欠落しているキーがあればデフォルト値で補完
                    for key, val in DEFAULT_CONFIG.items():
                        if key in loaded:
                            config[key] = loaded[key]
                        else:
                            config[key] = val
        except Exception as e:
            print(f"【警告】設定ファイルの読み込みに失敗したためデフォルト設定を使用します: {e}")
            config = DEFAULT_CONFIG.copy()
    else:
        # 初回起動時はデフォルト設定をファイルに書き出し
        save_config(config)

    _current_config = config
    return _current_config


def save_config(config: Dict[str, Any]) -> bool:
    """設定を config.json に安全にアトミック保存します。"""
    global _current_config
    _current_config = config
    temp_file = f"{CONFIG_FILE}.tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, CONFIG_FILE)
        return True
    except Exception as e:
        print(f"【エラー】設定の保存に失敗しました: {e}")
        return False


def get_config() -> Dict[str, Any]:
    """現在の設定キャッシュを取得します。未ロードの場合はロードします。"""
    global _current_config
    if not _current_config:
        load_config()
    return _current_config


def update_config_value(key: str, value: Any) -> bool:
    """特定の設定項目を更新して保存します。"""
    cfg = get_config()
    cfg[key] = value
    return save_config(cfg)
