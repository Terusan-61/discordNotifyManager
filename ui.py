"""
GUIモジュール (ui.py)
CustomTkinter を使用した Discord通知マネージャー のモダンな管理画面
- ログ一覧の表示・リアルタイム検索・フィルター（すべて / ピン留め / 本日）
- 📌 ピン留め（あとで読む）機能
- 🚀 Discordアプリのアクティブ化機能
- 🧹 ログのワンクリック全消去機能
- ⚙️ 設定（自動削除秒数・ログ上限など）の一元管理機能
"""

import datetime
import json
import os
import sys
import tkinter.messagebox as messagebox
from typing import Any, Callable, Dict, List, Optional

import customtkinter as ctk

import config

# Discord風のダークテーマカラー定義
COLOR_BG_DARK = "#1e1f22"       # ウィンドウ背景
COLOR_CARD_BG = "#2b2d31"       # カード背景
COLOR_CARD_HOVER = "#313338"    # カードホバー色
COLOR_BLURPLE = "#5865F2"       # Discordメインカラー (Blurple)
COLOR_BLURPLE_HOVER = "#4752C4"
COLOR_GREEN = "#57F287"         # 稼働状態・成功
COLOR_RED = "#ED4245"           # 削除・危険
COLOR_TEXT_MUTED = "#949ba4"    # サブテキスト色


class DiscordNotifyManagerUI(ctk.CTk):
    def __init__(self, log_file_path: str, on_config_changed: Optional[Callable[[Dict[str, Any]], None]] = None):
        super().__init__()

        self.log_file_path = log_file_path
        self.on_config_changed = on_config_changed

        # ウィンドウの基本設定
        self.title("Discord 通知マネージャー")
        self.geometry("780x640")
        self.minsize(680, 500)

        # CustomTkinterの外観モード設定
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # 内部データ
        self.all_logs: List[Dict[str, Any]] = []
        self.current_filter = "all"  # "all", "pinned", "today"
        self.search_keyword = ""

        # アイコンの設定（ファイルが存在する場合）
        icon_path = os.path.join(config.BASE_DIR, "icon.ico")
        if os.path.exists(icon_path):
            try:
                self.iconbitmap(icon_path)
            except Exception:
                pass

        # 画面の閉じるボタン [X] が押された時はアプリ終了ではなく非表示 (withdraw) にする
        self.protocol("WM_DELETE_WINDOW", self.hide_window)

        # UIコンポーネントの構築
        self._build_ui()

        # データの初回読み込み
        self.refresh_logs()

    def _build_ui(self):
        """UI全体のレイアウトを構築"""
        # メインコンテナ（タブビューで「ログ一覧」と「設定」を切り替え）
        self.tabview = ctk.CTkTabview(
            self,
            segmented_button_fg_color=COLOR_BG_DARK,
            segmented_button_selected_color=COLOR_BLURPLE,
            segmented_button_selected_hover_color=COLOR_BLURPLE_HOVER,
        )
        self.tabview.pack(fill="both", expand=True, padx=12, pady=10)

        self.tab_logs = self.tabview.add("📋 通知ログ一覧")
        self.tab_settings = self.tabview.add("⚙️ 設定")

        # 各タブの中身を構築
        self._build_logs_tab()
        self._build_settings_tab()

    # ==========================================================================
    # タブ1: 通知ログ一覧
    # ==========================================================================
    def _build_logs_tab(self):
        # 1. 検索バー & フィルター操作部（上部フレーム）
        top_bar = ctk.CTkFrame(self.tab_logs, fg_color="transparent")
        top_bar.pack(fill="x", padx=4, pady=(2, 8))

        # 検索入力ボックス
        self.search_entry = ctk.CTkEntry(
            top_bar,
            placeholder_text="🔍 ユーザー名・チャンネル・本文で検索...",
            height=36,
            font=("Meiryo", 12),
        )
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.search_entry.bind("<KeyRelease>", self._on_search_changed)

        # フィルター切り替えセグメントボタン
        self.filter_segment = ctk.CTkSegmentedButton(
            top_bar,
            values=["すべて", "📌 ピン留め", "本日"],
            command=self._on_filter_changed,
            selected_color=COLOR_BLURPLE,
            selected_hover_color=COLOR_BLURPLE_HOVER,
            height=36,
        )
        self.filter_segment.set("すべて")
        self.filter_segment.pack(side="left", padx=(0, 8))

        # 更新ボタン
        btn_refresh = ctk.CTkButton(
            top_bar,
            text="🔄 更新",
            width=65,
            height=36,
            fg_color="#3b3e45",
            hover_color="#474b53",
            command=self.refresh_logs,
        )
        btn_refresh.pack(side="left")

        # 2. 件数表示ラベル
        self.lbl_count = ctk.CTkLabel(
            self.tab_logs,
            text="読み込み中...",
            font=("Meiryo", 11),
            text_color=COLOR_TEXT_MUTED,
            anchor="w",
        )
        self.lbl_count.pack(fill="x", padx=6, pady=(0, 4))

        # 3. ログカード一覧表示用スクロールフレーム
        self.scroll_frame = ctk.CTkScrollableFrame(
            self.tab_logs,
            fg_color=COLOR_BG_DARK,
            corner_radius=8,
        )
        self.scroll_frame.pack(fill="both", expand=True, padx=4, pady=(0, 8))

        # 4. フッターバー（下部）
        bottom_bar = ctk.CTkFrame(self.tab_logs, fg_color="transparent")
        bottom_bar.pack(fill="x", padx=4, pady=(0, 4))

        # 🧹 全消去ボタン
        btn_clear = ctk.CTkButton(
            bottom_bar,
            text="🧹 ログを全消去",
            fg_color=COLOR_RED,
            hover_color="#c93b3e",
            height=32,
            font=("Meiryo", 11, "bold"),
            command=self._on_clear_all_logs,
        )
        btn_clear.pack(side="left")

        # 🚀 Discordを開く全体ボタン
        btn_open_discord = ctk.CTkButton(
            bottom_bar,
            text="🚀 Discordアプリを開く",
            fg_color=COLOR_BLURPLE,
            hover_color=COLOR_BLURPLE_HOVER,
            height=32,
            font=("Meiryo", 11, "bold"),
            command=self._open_discord,
        )
        btn_open_discord.pack(side="right")

    # ==========================================================================
    # タブ2: 設定
    # ==========================================================================
    def _build_settings_tab(self):
        container = ctk.CTkScrollableFrame(self.tab_settings, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=20, pady=15)

        # 項目1: 自動削除待機秒数
        lbl_sec_title = ctk.CTkLabel(container, text="⏱️ 通知の自動削除までの時間", font=("Meiryo", 14, "bold"), anchor="w")
        lbl_sec_title.pack(fill="x", pady=(10, 2))

        lbl_sec_desc = ctk.CTkLabel(
            container,
            text="Discord通知を受信してから画面および通知センターから削除するまでの秒数を設定します。",
            font=("Meiryo", 11),
            text_color=COLOR_TEXT_MUTED,
            anchor="w",
        )
        lbl_sec_desc.pack(fill="x", pady=(0, 8))

        delay_frame = ctk.CTkFrame(container, fg_color=COLOR_BG_DARK, corner_radius=8)
        delay_frame.pack(fill="x", pady=(0, 15), ipady=8, ipadx=10)

        self.lbl_delay_val = ctk.CTkLabel(delay_frame, text="3 秒", font=("Meiryo", 16, "bold"), width=70)
        self.lbl_delay_val.pack(side="right", padx=10)

        self.slider_delay = ctk.CTkSlider(
            delay_frame,
            from_=1,
            to=30,
            number_of_steps=29,
            command=self._on_delay_slider_changed,
            button_color=COLOR_BLURPLE,
            button_hover_color=COLOR_BLURPLE_HOVER,
        )
        self.slider_delay.pack(side="left", fill="x", expand=True, padx=10)

        # 項目2: ログ最大保持件数
        lbl_max_title = ctk.CTkLabel(container, text="📦 ログの最大保持件数（古いものから自動削除）", font=("Meiryo", 14, "bold"), anchor="w")
        lbl_max_title.pack(fill="x", pady=(10, 2))

        lbl_max_desc = ctk.CTkLabel(
            container,
            text="保存件数がこの上限を超えると、ピン留めされていない最も古いログから自動的に消去されます。",
            font=("Meiryo", 11),
            text_color=COLOR_TEXT_MUTED,
            anchor="w",
        )
        lbl_max_desc.pack(fill="x", pady=(0, 8))

        max_frame = ctk.CTkFrame(container, fg_color=COLOR_BG_DARK, corner_radius=8)
        max_frame.pack(fill="x", pady=(0, 15), ipady=8, ipadx=10)

        self.segment_max_count = ctk.CTkSegmentedButton(
            max_frame,
            values=["100 件", "300 件", "500 件", "1000 件", "3000 件"],
            selected_color=COLOR_BLURPLE,
            selected_hover_color=COLOR_BLURPLE_HOVER,
        )
        self.segment_max_count.pack(fill="x", padx=10, pady=5)

        # 項目3: 自動削除の有効 / 無効
        lbl_enable_title = ctk.CTkLabel(container, text="🔔 自動削除機能の有効化", font=("Meiryo", 14, "bold"), anchor="w")
        lbl_enable_title.pack(fill="x", pady=(10, 2))

        lbl_enable_desc = ctk.CTkLabel(
            container,
            text="オフにすると自動削除を停止し、通知ログの保存のみを行います（タスクトレイからも切り替え可能）。",
            font=("Meiryo", 11),
            text_color=COLOR_TEXT_MUTED,
            anchor="w",
        )
        lbl_enable_desc.pack(fill="x", pady=(0, 8))

        enable_frame = ctk.CTkFrame(container, fg_color=COLOR_BG_DARK, corner_radius=8)
        enable_frame.pack(fill="x", pady=(0, 25), ipady=8, ipadx=10)

        self.switch_auto_delete = ctk.CTkSwitch(
            enable_frame,
            text="自動削除を有効にする",
            font=("Meiryo", 12),
            progress_color=COLOR_BLURPLE,
        )
        self.switch_auto_delete.pack(side="left", padx=15, pady=5)

        # 保存ボタン
        btn_save_settings = ctk.CTkButton(
            container,
            text="💾 設定を保存する",
            fg_color=COLOR_BLURPLE,
            hover_color=COLOR_BLURPLE_HOVER,
            height=42,
            font=("Meiryo", 13, "bold"),
            command=self._on_save_settings,
        )
        btn_save_settings.pack(fill="x", pady=(10, 20))

        # 現在の設定値をフォームに反映
        self._load_settings_into_form()

    def _load_settings_into_form(self):
        """現在の設定値をフォームコントロールに反映"""
        cfg = config.get_config()
        # 秒数スライダー
        delay = int(cfg.get("auto_delete_delay_seconds", 3))
        self.slider_delay.set(delay)
        self.lbl_delay_val.configure(text=f"{delay} 秒")

        # 最大件数
        max_cnt = cfg.get("max_log_count", 500)
        cnt_str = f"{max_cnt} 件"
        if cnt_str in ["100 件", "300 件", "500 件", "1000 件", "3000 件"]:
            self.segment_max_count.set(cnt_str)
        else:
            self.segment_max_count.set("500 件")

        # 自動削除スイッチ
        if cfg.get("auto_delete_enabled", True):
            self.switch_auto_delete.select()
        else:
            self.switch_auto_delete.deselect()

    def _on_delay_slider_changed(self, value):
        self.lbl_delay_val.configure(text=f"{int(value)} 秒")

    def _on_save_settings(self):
        """設定フォームの値を config.json に保存"""
        cfg = config.get_config()

        cfg["auto_delete_delay_seconds"] = int(self.slider_delay.get())
        selected_cnt = self.segment_max_count.get()
        try:
            cfg["max_log_count"] = int(selected_cnt.replace(" 件", ""))
        except Exception:
            cfg["max_log_count"] = 500
        cfg["auto_delete_enabled"] = bool(self.switch_auto_delete.get())

        config.save_config(cfg)

        # 変更コールバックの呼び出し
        if self.on_config_changed:
            self.on_config_changed(cfg)

        messagebox.showinfo("設定完了", "設定を正常に保存しました！")

    # ==========================================================================
    # ログデータの読み込み・描画処理
    # ==========================================================================
    def refresh_logs(self):
        """JSONファイルからログを再読み込みして描画"""
        if os.path.exists(self.log_file_path):
            try:
                with open(self.log_file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.all_logs = data
                    else:
                        self.all_logs = []
            except Exception as e:
                print(f"ログ読み込みエラー: {e}")
                self.all_logs = []
        else:
            self.all_logs = []

        self._render_log_cards()

    def _render_log_cards(self):
        """現在の検索キーワード・フィルターに基づいてカードを描画"""
        # 既存のカードウィジェットを全て削除
        for child in self.scroll_frame.winfo_children():
            child.destroy()

        pinned_ids = set(config.get_config().get("pinned_notification_ids", []))
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")

        # フィルタリング
        filtered = []
        for item in self.all_logs:
            nid = item.get("id")
            title = item.get("title", "")
            body = item.get("body", "")
            rec_at = item.get("received_at", "")

            # 1. フィルタータブ条件
            if self.current_filter == "pinned" and nid not in pinned_ids:
                continue
            if self.current_filter == "today" and not rec_at.startswith(today_str):
                continue

            # 2. 検索キーワード条件（タイトル・本文の部分一致）
            if self.search_keyword:
                kw = self.search_keyword.lower()
                if kw not in title.lower() and kw not in body.lower():
                    continue

            filtered.append(item)

        # 最新の通知が一番上に来るように逆順表示
        filtered_reversed = list(reversed(filtered))

        # 件数表示の更新
        self.lbl_count.configure(
            text=f"表示中: {len(filtered_reversed)} 件 / 全 {len(self.all_logs)} 件"
        )

        if not filtered_reversed:
            lbl_empty = ctk.CTkLabel(
                self.scroll_frame,
                text="該当する通知ログはありません。",
                font=("Meiryo", 13),
                text_color=COLOR_TEXT_MUTED,
            )
            lbl_empty.pack(pady=40)
            return

        # 各ログのカードを生成
        for entry in filtered_reversed:
            self._create_log_card(entry, pinned_ids)

    def _create_log_card(self, entry: Dict[str, Any], pinned_ids: set):
        """1件の通知を表示するカードウィジェットを作成"""
        nid = entry.get("id")
        title = entry.get("title", "（タイトルなし）")
        body = entry.get("body", "")
        received_at = entry.get("received_at", "")
        is_pinned = nid in pinned_ids

        # カード外枠
        card = ctk.CTkFrame(self.scroll_frame, fg_color=COLOR_CARD_BG, corner_radius=8)
        card.pack(fill="x", padx=4, pady=4, ipady=4)

        # ヘッダー部（送信元・日時・ピン留めボタン）
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=(6, 2))

        # 送信元（チャンネル / ユーザー名）
        lbl_title = ctk.CTkLabel(
            header,
            text=f"🔔 {title}",
            font=("Meiryo", 12, "bold"),
            anchor="w",
        )
        lbl_title.pack(side="left", fill="x", expand=True)

        # 受信日時
        lbl_time = ctk.CTkLabel(
            header,
            text=received_at,
            font=("Meiryo", 10),
            text_color=COLOR_TEXT_MUTED,
        )
        lbl_time.pack(side="left", padx=8)

        # 📌 ピン留めトグルボタン
        pin_btn_text = "📌 ピン留め中" if is_pinned else "📌 ピン留め"
        pin_btn_color = COLOR_BLURPLE if is_pinned else "#3b3e45"
        btn_pin = ctk.CTkButton(
            header,
            text=pin_btn_text,
            width=80,
            height=26,
            fg_color=pin_btn_color,
            hover_color=COLOR_BLURPLE_HOVER,
            font=("Meiryo", 10),
            command=lambda i=nid: self._toggle_pin(i),
        )
        btn_pin.pack(side="right")

        # 本文
        if body:
            lbl_body = ctk.CTkLabel(
                card,
                text=body,
                font=("Meiryo", 11),
                anchor="w",
                justify="left",
                wraplength=640,
            )
            lbl_body.pack(fill="x", padx=14, pady=(2, 6))

        # アクション行（🚀 Discordを開くボタン）
        action_row = ctk.CTkFrame(card, fg_color="transparent")
        action_row.pack(fill="x", padx=10, pady=(0, 4))

        btn_jump = ctk.CTkButton(
            action_row,
            text="🚀 Discordを開く",
            width=100,
            height=24,
            fg_color="#3b3e45",
            hover_color=COLOR_BLURPLE,
            font=("Meiryo", 10),
            command=self._open_discord,
        )
        btn_jump.pack(side="right")

    # ==========================================================================
    # イベントハンドラー
    # ==========================================================================
    def _on_search_changed(self, event=None):
        self.search_keyword = self.search_entry.get().strip()
        self._render_log_cards()

    def _on_filter_changed(self, value):
        if value == "📌 ピン留め":
            self.current_filter = "pinned"
        elif value == "本日":
            self.current_filter = "today"
        else:
            self.current_filter = "all"
        self._render_log_cards()

    def _toggle_pin(self, notif_id: int):
        """ピン留めのON/OFFをトグル"""
        cfg = config.get_config()
        pinned_list: List[int] = cfg.get("pinned_notification_ids", [])

        if notif_id in pinned_list:
            pinned_list.remove(notif_id)
        else:
            pinned_list.append(notif_id)

        config.update_config_value("pinned_notification_ids", pinned_list)
        self._render_log_cards()

    def _on_clear_all_logs(self):
        """ログの全消去"""
        if not self.all_logs:
            messagebox.showinfo("情報", "消去するログがありません。")
            return

        res = messagebox.askyesno(
            "ログの全消去の確認",
            f"保存されている全 {len(self.all_logs)} 件の通知ログを消去しますか？\n（※この操作は取り消せません）",
        )
        if res:
            try:
                with open(self.log_file_path, "w", encoding="utf-8") as f:
                    f.write("[]\n")
                self.all_logs = []
                self._render_log_cards()
                messagebox.showinfo("完了", "すべての通知ログを消去しました。")
            except Exception as e:
                messagebox.showerror("エラー", f"ログの消去に失敗しました: {e}")

    def _open_discord(self):
        """Discordクライアントを最前面にアクティブ化"""
        try:
            os.startfile("discord://")
        except Exception as e:
            messagebox.showerror("エラー", f"Discordの起動に失敗しました: {e}")

    # ==========================================================================
    # ウィンドウの表示・非表示制御
    # ==========================================================================
    def show_window(self):
        """ウィンドウを表示して最前面にフォーカス"""
        self.deiconify()
        self.lift()
        self.focus_force()
        self.refresh_logs()
        self._load_settings_into_form()

    def hide_window(self):
        """ウィンドウを非表示（タスクトレイに格納）"""
        self.withdraw()
