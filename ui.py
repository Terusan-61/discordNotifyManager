"""
GUIモジュール (ui.py)
CustomTkinter を使用した Discord通知マネージャー のモダンな管理画面
- ログ一覧の表示・Enter/ボタン検索・フィルター（すべて / ピン留め / 本日）
- 📄 ページネーション（ページめくり）機能（描画負荷・メモリ消費を大幅低減）
- ✂️ 本文の長文制限（300文字超の自動省略）
- 📌 ピン留め（あとで読む）機能
- 🚀 Discordアプリのアクティブ化機能
- 🧹 ログのワンクリック全消去機能
- ⚙️ 設定の一元管理（自動削除秒数、最大件数、1ページ表示件数、ログ保存切替）
"""

import datetime
import json
import math
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

# 本文の最大プレビュー文字数（長文によるUI圧迫を防止）
MAX_BODY_PREVIEW_LENGTH = 300


class DiscordNotifyManagerUI(ctk.CTk):
    def __init__(self, log_file_path: str, on_config_changed: Optional[Callable[[Dict[str, Any]], None]] = None):
        super().__init__()

        self.log_file_path = log_file_path
        self.on_config_changed = on_config_changed

        # ウィンドウの基本設定
        self.title("Discord 通知マネージャー")
        self.geometry("820x680")
        self.minsize(720, 540)

        # CustomTkinterの外観モード設定
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # 内部データ
        self.all_logs: List[Dict[str, Any]] = []
        self.current_filter = "all"  # "all", "pinned", "today"
        self.search_keyword = ""

        # ページネーション管理
        self.current_page = 1
        self.total_pages = 1
        self.items_per_page = int(config.get_config().get("items_per_page", 20))

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
        self.tabview = ctk.CTkTabview(
            self,
            segmented_button_fg_color=COLOR_BG_DARK,
            segmented_button_selected_color=COLOR_BLURPLE,
            segmented_button_selected_hover_color=COLOR_BLURPLE_HOVER,
        )
        self.tabview.pack(fill="both", expand=True, padx=12, pady=8)

        self.tab_logs = self.tabview.add("📋 通知ログ一覧")
        self.tab_settings = self.tabview.add("⚙️ 設定")

        self._build_logs_tab()
        self._build_settings_tab()

    # ==========================================================================
    # タブ1: 通知ログ一覧
    # ==========================================================================
    def _build_logs_tab(self):
        # 1. 検索バー & フィルター操作部（上部フレーム）
        top_bar = ctk.CTkFrame(self.tab_logs, fg_color="transparent")
        top_bar.pack(fill="x", padx=4, pady=(2, 6))

        # 検索入力ボックス (Enterキーで検索実行)
        self.search_entry = ctk.CTkEntry(
            top_bar,
            placeholder_text="🔍 キーワード入力後、Enterキーまたは検索ボタンを押してください...",
            height=36,
            font=("Meiryo", 11),
        )
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.search_entry.bind("<Return>", self._on_execute_search)

        # 🔍 検索ボタン
        btn_search = ctk.CTkButton(
            top_bar,
            text="🔍 検索",
            width=68,
            height=36,
            fg_color=COLOR_BLURPLE,
            hover_color=COLOR_BLURPLE_HOVER,
            command=self._on_execute_search,
        )
        btn_search.pack(side="left", padx=(0, 6))

        # ✖ クリアボタン
        btn_clear_search = ctk.CTkButton(
            top_bar,
            text="✖ クリア",
            width=65,
            height=36,
            fg_color="#3b3e45",
            hover_color="#474b53",
            command=self._on_clear_search,
        )
        btn_clear_search.pack(side="left", padx=(0, 8))

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
        self.filter_segment.pack(side="left", padx=(0, 6))

        # 🔄 更新ボタン
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
        self.scroll_frame.pack(fill="both", expand=True, padx=4, pady=(0, 6))

        # 4. ページネーション操作バー（ページめくり）
        page_bar = ctk.CTkFrame(self.tab_logs, fg_color="transparent")
        page_bar.pack(fill="x", padx=4, pady=(2, 6))

        # ページ切り替えコントロールを中央揃えで配置するインナーフレーム
        page_controls = ctk.CTkFrame(page_bar, fg_color="transparent")
        page_controls.pack(anchor="center")

        self.btn_first_page = ctk.CTkButton(
            page_controls,
            text="⏪ 最初",
            width=65,
            height=30,
            fg_color="#3b3e45",
            hover_color="#474b53",
            font=("Meiryo", 10),
            command=self._go_first_page,
        )
        self.btn_first_page.pack(side="left", padx=4)

        self.btn_prev_page = ctk.CTkButton(
            page_controls,
            text="◀ 前へ",
            width=65,
            height=30,
            fg_color=COLOR_BLURPLE,
            hover_color=COLOR_BLURPLE_HOVER,
            font=("Meiryo", 10),
            command=self._go_prev_page,
        )
        self.btn_prev_page.pack(side="left", padx=4)

        self.lbl_page_info = ctk.CTkLabel(
            page_controls,
            text="1 / 1 ページ",
            font=("Meiryo", 12, "bold"),
            width=110,
        )
        self.lbl_page_info.pack(side="left", padx=8)

        self.btn_next_page = ctk.CTkButton(
            page_controls,
            text="次へ ▶",
            width=65,
            height=30,
            fg_color=COLOR_BLURPLE,
            hover_color=COLOR_BLURPLE_HOVER,
            font=("Meiryo", 10),
            command=self._go_next_page,
        )
        self.btn_next_page.pack(side="left", padx=4)

        self.btn_last_page = ctk.CTkButton(
            page_controls,
            text="最後 ⏩",
            width=65,
            height=30,
            fg_color="#3b3e45",
            hover_color="#474b53",
            font=("Meiryo", 10),
            command=self._go_last_page,
        )
        self.btn_last_page.pack(side="left", padx=4)

        # 5. フッターバー（最下部）
        bottom_bar = ctk.CTkFrame(self.tab_logs, fg_color="transparent")
        bottom_bar.pack(fill="x", padx=4, pady=(2, 4))

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

        # 項目2: 1ページあたりの表示件数（ページネーション設定）
        lbl_page_title = ctk.CTkLabel(container, text="📄 1ページあたりの表示件数", font=("Meiryo", 14, "bold"), anchor="w")
        lbl_page_title.pack(fill="x", pady=(10, 2))

        lbl_page_desc = ctk.CTkLabel(
            container,
            text="一覧画面で1ページに表示する通知カードの件数を設定します。件数を抑えることで描画が非常に軽快になります。",
            font=("Meiryo", 11),
            text_color=COLOR_TEXT_MUTED,
            anchor="w",
        )
        lbl_page_desc.pack(fill="x", pady=(0, 8))

        page_setting_frame = ctk.CTkFrame(container, fg_color=COLOR_BG_DARK, corner_radius=8)
        page_setting_frame.pack(fill="x", pady=(0, 15), ipady=8, ipadx=10)

        self.segment_items_per_page = ctk.CTkSegmentedButton(
            page_setting_frame,
            values=["10 件", "20 件", "50 件"],
            selected_color=COLOR_BLURPLE,
            selected_hover_color=COLOR_BLURPLE_HOVER,
        )
        self.segment_items_per_page.pack(fill="x", padx=10, pady=5)

        # 項目3: ログ最大保持件数 (直接数値入力)
        lbl_max_title = ctk.CTkLabel(container, text="📦 ログの最大保持件数（直接数値入力）", font=("Meiryo", 14, "bold"), anchor="w")
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

        self.entry_max_count = ctk.CTkEntry(
            max_frame,
            width=140,
            height=36,
            font=("Meiryo", 13, "bold"),
            placeholder_text="例: 500",
        )
        self.entry_max_count.pack(side="left", padx=(12, 6), pady=6)

        lbl_unit = ctk.CTkLabel(max_frame, text="件", font=("Meiryo", 13))
        lbl_unit.pack(side="left", padx=(0, 15))

        for preset_val in [100, 500, 1000]:
            btn_preset = ctk.CTkButton(
                max_frame,
                text=f"{preset_val}件",
                width=65,
                height=30,
                fg_color="#3b3e45",
                hover_color="#474b53",
                font=("Meiryo", 10),
                command=lambda v=preset_val: self._set_max_count_preset(v),
            )
            btn_preset.pack(side="left", padx=4)

        # 項目4: 自動削除機能の有効 / 無効
        lbl_enable_title = ctk.CTkLabel(container, text="🔔 自動削除機能の有効化", font=("Meiryo", 14, "bold"), anchor="w")
        lbl_enable_title.pack(fill="x", pady=(10, 2))

        lbl_enable_desc = ctk.CTkLabel(
            container,
            text="オフにすると自動削除を停止します（タスクトレイからもワンクリックで切り替え可能）。",
            font=("Meiryo", 11),
            text_color=COLOR_TEXT_MUTED,
            anchor="w",
        )
        lbl_enable_desc.pack(fill="x", pady=(0, 8))

        enable_frame = ctk.CTkFrame(container, fg_color=COLOR_BG_DARK, corner_radius=8)
        enable_frame.pack(fill="x", pady=(0, 15), ipady=8, ipadx=10)

        self.switch_auto_delete = ctk.CTkSwitch(
            enable_frame,
            text="自動削除を有効にする",
            font=("Meiryo", 12),
            progress_color=COLOR_BLURPLE,
        )
        self.switch_auto_delete.pack(side="left", padx=15, pady=5)

        # 項目5: ログ保存機能の有効 / 無効
        lbl_save_title = ctk.CTkLabel(container, text="📝 通知ログの保存機能の有効化", font=("Meiryo", 14, "bold"), anchor="w")
        lbl_save_title.pack(fill="x", pady=(10, 2))

        lbl_save_desc = ctk.CTkLabel(
            container,
            text="オフにすると、通知ログをファイルに保存しません。「自動削除のみを行いたい」場合に便利です。",
            font=("Meiryo", 11),
            text_color=COLOR_TEXT_MUTED,
            anchor="w",
        )
        lbl_save_desc.pack(fill="x", pady=(0, 8))

        save_frame = ctk.CTkFrame(container, fg_color=COLOR_BG_DARK, corner_radius=8)
        save_frame.pack(fill="x", pady=(0, 25), ipady=8, ipadx=10)

        self.switch_save_log = ctk.CTkSwitch(
            save_frame,
            text="通知ログを保存する",
            font=("Meiryo", 12),
            progress_color=COLOR_BLURPLE,
        )
        self.switch_save_log.pack(side="left", padx=15, pady=5)

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

    def _set_max_count_preset(self, val: int):
        self.entry_max_count.delete(0, "end")
        self.entry_max_count.insert(0, str(val))

    def _load_settings_into_form(self):
        """現在の設定値をフォームコントロールに反映"""
        cfg = config.get_config()

        # 秒数スライダー
        delay = int(cfg.get("auto_delete_delay_seconds", 3))
        self.slider_delay.set(delay)
        self.lbl_delay_val.configure(text=f"{delay} 秒")

        # 1ページあたりの件数
        per_page = int(cfg.get("items_per_page", 20))
        self.items_per_page = per_page
        per_page_str = f"{per_page} 件"
        if per_page_str in ["10 件", "20 件", "50 件"]:
            self.segment_items_per_page.set(per_page_str)
        else:
            self.segment_items_per_page.set("20 件")

        # 最大件数（数値エントリ）
        max_cnt = cfg.get("max_log_count", 500)
        self.entry_max_count.delete(0, "end")
        self.entry_max_count.insert(0, str(max_cnt))

        # 自動削除スイッチ
        if cfg.get("auto_delete_enabled", True):
            self.switch_auto_delete.select()
        else:
            self.switch_auto_delete.deselect()

        # ログ保存スイッチ
        if cfg.get("save_log_enabled", True):
            self.switch_save_log.select()
        else:
            self.switch_save_log.deselect()

    def _on_delay_slider_changed(self, value):
        self.lbl_delay_val.configure(text=f"{int(value)} 秒")

    def _on_save_settings(self):
        """設定フォームの値を config.json に保存"""
        cfg = config.get_config()

        # 秒数
        cfg["auto_delete_delay_seconds"] = int(self.slider_delay.get())

        # 1ページあたりの件数
        selected_per_page = self.segment_items_per_page.get()
        try:
            self.items_per_page = int(selected_per_page.replace(" 件", ""))
        except Exception:
            self.items_per_page = 20
        cfg["items_per_page"] = self.items_per_page

        # 最大件数のバリデーション
        raw_max = self.entry_max_count.get().strip()
        try:
            val = int(raw_max)
            if val <= 0:
                raise ValueError("正の整数である必要があります")
            cfg["max_log_count"] = val
        except ValueError:
            messagebox.showerror("入力エラー", "ログ最大保持件数には1以上の半角数字を入力してください。")
            return

        # スイッチ
        cfg["auto_delete_enabled"] = bool(self.switch_auto_delete.get())
        cfg["save_log_enabled"] = bool(self.switch_save_log.get())

        config.save_config(cfg)

        if self.on_config_changed:
            self.on_config_changed(cfg)

        # ページを1に戻して再描画
        self.current_page = 1
        self._render_log_cards()

        messagebox.showinfo("設定完了", "設定を正常に保存しました！")

    # ==========================================================================
    # ページネーション操作
    # ==========================================================================
    def _go_first_page(self):
        if self.current_page > 1:
            self.current_page = 1
            self._render_log_cards()

    def _go_prev_page(self):
        if self.current_page > 1:
            self.current_page -= 1
            self._render_log_cards()

    def _go_next_page(self):
        if self.current_page < self.total_pages:
            self.current_page += 1
            self._render_log_cards()

    def _go_last_page(self):
        if self.current_page < self.total_pages:
            self.current_page = self.total_pages
            self._render_log_cards()

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

    def _reset_scroll_position(self):
        """スクロール位置を確実に最上部にリセットするヘルパー"""
        try:
            if hasattr(self.scroll_frame, "_parent_canvas"):
                self.scroll_frame._parent_canvas.yview_moveto(0.0)
        except Exception:
            pass

    def _render_log_cards(self):
        """現在の検索キーワード・フィルター・ページ番号に基づいてカードを描画"""
        self._reset_scroll_position()

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

            # 2. 検索キーワード条件
            if self.search_keyword:
                kw = self.search_keyword.lower()
                if kw not in title.lower() and kw not in body.lower():
                    continue

            filtered.append(item)

        # 最新の通知が一番上に来るように逆順
        filtered_reversed = list(reversed(filtered))
        total_items = len(filtered_reversed)

        # ページネーションの計算
        self.total_pages = max(1, math.ceil(total_items / self.items_per_page))
        self.current_page = max(1, min(self.current_page, self.total_pages))

        # ページネーションボタンの活性/非活性状態の更新
        self.btn_first_page.configure(state="normal" if self.current_page > 1 else "disabled")
        self.btn_prev_page.configure(state="normal" if self.current_page > 1 else "disabled")
        self.btn_next_page.configure(state="normal" if self.current_page < self.total_pages else "disabled")
        self.btn_last_page.configure(state="normal" if self.current_page < self.total_pages else "disabled")
        self.lbl_page_info.configure(text=f"{self.current_page} / {self.total_pages} ページ")

        # 表示範囲のスライス計算
        start_idx = (self.current_page - 1) * self.items_per_page
        end_idx = min(start_idx + self.items_per_page, total_items)
        page_items = filtered_reversed[start_idx:end_idx]

        # 件数表示の更新
        search_hint = f"（検索: '{self.search_keyword}'）" if self.search_keyword else ""
        if total_items > 0:
            range_str = f"{start_idx + 1}〜{end_idx} 件目"
            self.lbl_count.configure(
                text=f"表示中: {range_str} / 該当 {total_items} 件 (全 {len(self.all_logs)} 件) {search_hint}"
            )
        else:
            self.lbl_count.configure(
                text=f"表示中: 0 件 / 該当 0 件 (全 {len(self.all_logs)} 件) {search_hint}"
            )

        if not page_items:
            lbl_empty = ctk.CTkLabel(
                self.scroll_frame,
                text="該当する通知ログはありません。",
                font=("Meiryo", 13),
                text_color=COLOR_TEXT_MUTED,
            )
            lbl_empty.pack(pady=40)
            return

        # 該当ページの通知カードのみを生成（負荷最小限！）
        for entry in page_items:
            self._create_log_card(entry, pinned_ids)

        # 描画完了後にスクロール領域を再計算し、最上部に固定
        self.scroll_frame.update_idletasks()
        self._reset_scroll_position()

    def _create_log_card(self, entry: Dict[str, Any], pinned_ids: set):
        """1件の通知を表示するカードウィジェットを作成"""
        nid = entry.get("id")
        title = entry.get("title", "（タイトルなし）")
        body = entry.get("body", "")
        received_at = entry.get("received_at", "")
        is_pinned = nid in pinned_ids

        # カード外枠
        card = ctk.CTkFrame(self.scroll_frame, fg_color=COLOR_CARD_BG, corner_radius=8)
        card.pack(fill="x", expand=True, padx=6, pady=4, ipady=4)

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
            width=85,
            height=26,
            fg_color=pin_btn_color,
            hover_color=COLOR_BLURPLE_HOVER,
            font=("Meiryo", 10),
            command=lambda i=nid: self._toggle_pin(i),
        )
        btn_pin.pack(side="right")

        # 本文（長文の場合は自動省略してUI圧迫を防止）
        if body:
            display_body = body
            if len(body) > MAX_BODY_PREVIEW_LENGTH:
                display_body = body[:MAX_BODY_PREVIEW_LENGTH] + "\n... (以下長文のため省略)"

            lbl_body = ctk.CTkLabel(
                card,
                text=display_body,
                font=("Meiryo", 11),
                anchor="w",
                justify="left",
                wraplength=660,
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
    def _on_execute_search(self, event=None):
        """Enterキーまたは検索ボタンが押された時に検索を実行"""
        self.search_keyword = self.search_entry.get().strip()
        self.current_page = 1  # 検索時は1ページ目に戻す
        self._render_log_cards()

    def _on_clear_search(self):
        """検索ワードをクリアして全件表示に戻す"""
        self.search_entry.delete(0, "end")
        self.search_keyword = ""
        self.current_page = 1
        self._render_log_cards()

    def _on_filter_changed(self, value):
        if value == "📌 ピン留め":
            self.current_filter = "pinned"
        elif value == "本日":
            self.current_filter = "today"
        else:
            self.current_filter = "all"
        self.current_page = 1  # フィルター切り替え時は1ページ目に戻す
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
                self.current_page = 1
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
