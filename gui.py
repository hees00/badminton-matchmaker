"""tkinter 화면: 명단 입력 → 설정 → 대진 생성 → 결과 보기 / 엑셀 저장."""
import copy
import math
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import tkinter.font as tkfont

from excel_io import load_players, review_rows, save_players, save_schedule, team_text
from models import GENDERS, GRADES, find_pairs, make_player
from scheduler import (GENDER_MODES, KIND_EVENT, MODE_FREE, MODE_MIXED,
                       ScheduleError, Settings, generate)

TITLE = "배드민턴 대진표 생성 🏸"
KOREAN_FONTS = ["Pretendard", "Malgun Gothic", "맑은 고딕", "AppleSDGothicNeo", "Apple SD Gothic Neo",
                "AppleGothic", "NanumGothic", "Noto Sans CJK KR", "Noto Sans KR", "WenQuanYi Zen Hei"]

# 색상: 연두 + 흰색
BG = "#F3F7EC"          # 바탕
CARD = "#FFFFFF"        # 카드
BORDER = "#DCE6CF"
LIME = "#9BD34A"        # 메인 연두 (버튼, 선택)
LIME_HOVER = "#86C232"
LIME_SOFT = "#EAF6D9"   # 표 머리글, 안내 상자
LIME_DEEP = "#3C6A0E"   # 연두 위 글자
ON_LIME = "#1D3505"     # 연두 버튼 글자
TEXT = "#1F2A1A"
MUTED = "#6E7B67"
DISABLED = "#DCE5D0"
ROW_STRIPE = "#F8FBF3"
ROW_ROUND = "#E3F1CF"   # 라운드 머리 줄
ROW_EVENT = "#FFF1DE"   # 이벤트 게임
ROW_EXTRA = "#FFF8D2"   # 한 게임 더 하는 사람
ROW_REVIEW = "#FDE4E1"  # 검토 필요 경기
REVIEW_TEXT = "#9A2C1C"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.players = []
        self.schedule = None
        self.editing = None  # 수정 중인 참가자 인덱스
        self._job = None     # 대진 생성 스레드 상태

        root.title(TITLE)
        root.geometry("1320x860")
        root.minsize(1080, 720)
        root.configure(bg=BG)
        self._setup_style()
        self._build_header()

        paned = ttk.PanedWindow(root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=14, pady=(12, 14))
        left = ttk.Frame(paned, style="App.TFrame")
        right = ttk.Frame(paned, style="App.TFrame")
        paned.add(left, weight=2)
        paned.add(right, weight=3)

        # 설정 → 입력 카드를 아래부터 쌓아서, 창이 작으면 명단 표만 줄어들게 해요
        self._build_settings(left)
        self._build_form(left)
        self._build_roster(left)
        self._build_results(right)
        self._refresh_roster()
        # 왼쪽 입력 영역이 잘리지 않게, 창 크기가 정해지면 한 번만 너비를 잡아줘요
        def place_sash(event):
            if event.width > 600:
                paned.sashpos(0, 500)
                paned.unbind("<Configure>")
        paned.bind("<Configure>", place_sash)

    # ----- 화면 구성 ------------------------------------------------------

    def _setup_style(self):
        families = set(tkfont.families(self.root))
        family = next((f for f in KOREAN_FONTS if f in families), None)
        self.family = family or tkfont.nametofont("TkDefaultFont").actual("family")
        if family:
            for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
                tkfont.nametofont(name).configure(family=family, size=10)
        f = self.family
        style = ttk.Style(self.root)
        style.theme_use("clam")
        flat = dict(bordercolor=BORDER, lightcolor=CARD, darkcolor=CARD)

        # 바탕 / 카드
        style.configure(".", background=CARD, foreground=TEXT, font=(f, 10),
                        troughcolor=LIME_SOFT, focuscolor=LIME, selectbackground=LIME,
                        selectforeground=ON_LIME)
        style.configure("TFrame", background=CARD)
        style.configure("App.TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD, relief="solid", borderwidth=1, **flat)
        style.configure("Header.TFrame", background=CARD)
        style.configure("TLabel", background=CARD, foreground=TEXT)
        style.configure("Title.TLabel", font=(f, 17, "bold"), foreground=TEXT)
        style.configure("Subtitle.TLabel", font=(f, 10), foreground=MUTED)
        style.configure("CardTitle.TLabel", font=(f, 12, "bold"))
        style.configure("Muted.TLabel", foreground=MUTED, font=(f, 9))
        style.configure("Chip.TLabel", background=LIME_SOFT, foreground=LIME_DEEP,
                        font=(f, 9, "bold"), padding=(10, 3))
        style.configure("Info.TLabel", background=LIME_SOFT, foreground=LIME_DEEP, padding=(10, 7))
        style.configure("Status.TLabel", foreground=MUTED)

        # 버튼: 기본은 흰 바탕 테두리, 강조는 연두
        style.configure("TButton", background=CARD, foreground=TEXT, padding=(12, 6),
                        relief="flat", focusthickness=0, focuscolor=CARD, **flat)
        style.map("TButton",
                  background=[("disabled", CARD), ("pressed", LIME_SOFT), ("active", LIME_SOFT)],
                  bordercolor=[("active", LIME)],
                  foreground=[("disabled", "#B3BEAA")])
        style.configure("Accent.TButton", background=LIME, foreground=ON_LIME, font=(f, 10, "bold"),
                        bordercolor=LIME, lightcolor=LIME, darkcolor=LIME, focuscolor=LIME)
        style.map("Accent.TButton",
                  background=[("disabled", DISABLED), ("pressed", LIME_HOVER), ("active", LIME_HOVER)],
                  bordercolor=[("disabled", DISABLED), ("active", LIME_HOVER)],
                  lightcolor=[("disabled", DISABLED), ("active", LIME_HOVER)],
                  darkcolor=[("disabled", DISABLED), ("active", LIME_HOVER)],
                  foreground=[("disabled", "#8E9B85")])
        style.configure("Big.Accent.TButton", font=(f, 12, "bold"), padding=(22, 10))
        style.configure("Danger.TButton", foreground="#B5462F")

        # 입력칸
        field = dict(fieldbackground=CARD, background=CARD, foreground=TEXT, arrowcolor=LIME_DEEP,
                     padding=4, insertcolor=TEXT, **flat)
        for name in ("TEntry", "TCombobox", "TSpinbox"):
            style.configure(name, **field)
            style.map(name, bordercolor=[("focus", LIME)], lightcolor=[("focus", LIME_SOFT)],
                      fieldbackground=[("readonly", CARD), ("disabled", BG)],
                      selectbackground=[("readonly", CARD)], selectforeground=[("readonly", TEXT)],
                      background=[("active", LIME_SOFT)])
        self.root.option_add("*TCombobox*Listbox.background", CARD)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", LIME)
        self.root.option_add("*TCombobox*Listbox.selectForeground", ON_LIME)
        self.root.option_add("*TCombobox*Listbox.font", (f, 10))

        style.configure("TCheckbutton", background=CARD, foreground=TEXT, indicatorbackground=CARD,
                        indicatorforeground=ON_LIME, upperbordercolor=BORDER, lowerbordercolor=BORDER,
                        focuscolor=CARD)
        style.map("TCheckbutton", background=[("active", CARD)],
                  indicatorbackground=[("selected", LIME), ("disabled", BG)],
                  foreground=[("disabled", "#B3BEAA")])

        # 표
        style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT,
                        rowheight=28, borderwidth=0, relief="flat", font=(f, 10), **flat)
        style.map("Treeview", background=[("selected", LIME)], foreground=[("selected", ON_LIME)])
        style.configure("Treeview.Heading", background=LIME_SOFT, foreground=LIME_DEEP,
                        font=(f, 10, "bold"), relief="flat", padding=(4, 7),
                        bordercolor=LIME_SOFT, lightcolor=LIME_SOFT, darkcolor=LIME_SOFT)
        style.map("Treeview.Heading", background=[("active", ROW_ROUND)])
        style.configure("Table.TFrame", background=CARD, relief="solid", borderwidth=1, **flat)

        for orient in ("Vertical", "Horizontal"):
            style.configure(f"{orient}.TScrollbar", background="#D6E2C8", troughcolor=CARD,
                            arrowcolor=MUTED, gripcount=0, relief="flat", arrowsize=12,
                            bordercolor=CARD, lightcolor="#D6E2C8", darkcolor="#D6E2C8")
            style.map(f"{orient}.TScrollbar", background=[("active", LIME)],
                      lightcolor=[("active", LIME)], darkcolor=[("active", LIME)])

        # 탭
        style.configure("TNotebook", background=CARD, borderwidth=0, tabmargins=(0, 0, 0, 0),
                        bordercolor=CARD, lightcolor=CARD, darkcolor=CARD)
        style.configure("TNotebook.Tab", background=CARD, foreground=MUTED, font=(f, 10, "bold"),
                        padding=(18, 8), borderwidth=0, bordercolor=CARD, lightcolor=CARD)
        style.map("TNotebook.Tab",
                  background=[("selected", LIME), ("active", LIME_SOFT)],
                  foreground=[("selected", ON_LIME), ("active", LIME_DEEP)],
                  lightcolor=[("selected", LIME)], bordercolor=[("selected", LIME)],
                  expand=[("selected", (0, 0, 0, 0))])

        style.configure("Lime.Horizontal.TProgressbar", background=LIME, troughcolor=LIME_SOFT,
                        bordercolor=LIME_SOFT, lightcolor=LIME, darkcolor=LIME, thickness=10)
        style.configure("TPanedwindow", background=BG)
        style.configure("Sash", sashthickness=14, gripcount=0, background=BG,
                        lightcolor=BG, bordercolor=BG)

    def _build_header(self):
        bar = ttk.Frame(self.root, style="Header.TFrame", padding=(20, 12))
        bar.pack(fill="x")
        ttk.Label(bar, text="🏸", font=(self.family, 22), background=LIME, foreground=ON_LIME,
                  padding=(10, 2)).pack(side="left")
        titles = ttk.Frame(bar, style="Header.TFrame")
        titles.pack(side="left", padx=12)
        ttk.Label(titles, text="배드민턴 대진표 생성", style="Title.TLabel").pack(anchor="w")
        ttk.Label(titles, text="복식 대진을 실력 · 성별 · 휴식까지 고려해서 자동으로 짜드려요",
                  style="Subtitle.TLabel").pack(anchor="w")
        tk.Frame(self.root, bg=LIME, height=3).pack(fill="x")

    def _card(self, parent, title, expand=False, pady=0, side="top"):
        """흰 카드 + 왼쪽 연두 막대가 있는 제목. (카드, 제목 줄) 을 돌려줘요."""
        card = ttk.Frame(parent, style="Card.TFrame", padding=(16, 10, 16, 12))
        card.pack(side=side, fill="both" if expand else "x", expand=expand, pady=pady)
        head = ttk.Frame(card)
        head.pack(fill="x", pady=(0, 8))
        tk.Frame(head, bg=LIME, width=4, height=20).pack(side="left", padx=(0, 8))
        ttk.Label(head, text=title, style="CardTitle.TLabel").pack(side="left")
        return card, head

    def _tree(self, parent, columns, widths, height=10):
        frame = ttk.Frame(parent, style="Table.TFrame", padding=1)
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=height)
        tree.tag_configure("stripe", background=ROW_STRIPE)
        for col, w in zip(columns, widths):
            tree.heading(col, text=col)
            tree.column(col, width=w, minwidth=30, anchor="center", stretch=True)
        ys = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        xs = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return frame, tree

    def _build_roster(self, parent):
        box, head = self._card(parent, "참가자 명단", expand=True, pady=(0, 10))
        self.roster_count = ttk.Label(head, style="Chip.TLabel")
        self.roster_count.pack(side="left", padx=8)
        frame, self.roster = self._tree(
            box, ("이름", "나이", "성별", "급수", "고정 파트너"),
            (90, 50, 50, 50, 90), height=4)  # 남는 높이만큼 늘어나요
        frame.pack(fill="both", expand=True)
        self.roster.bind("<<TreeviewSelect>>", self._on_select)
        self.roster.bind("<Delete>", lambda e: self._delete())

        bar = ttk.Frame(box)
        bar.pack(fill="x", pady=(10, 0))
        ttk.Button(bar, text="📂 불러오기", command=self._load).pack(side="left")
        ttk.Button(bar, text="💾 명단 저장", command=self._save_roster).pack(side="left", padx=6)
        ttk.Button(bar, text="전체 삭제", style="Danger.TButton", command=self._clear).pack(side="right")
        ttk.Button(bar, text="삭제", command=self._delete).pack(side="right", padx=6)

    def _build_form(self, parent):
        box, _ = self._card(parent, "참가자 추가 / 수정", pady=(0, 10), side="bottom")
        self.v_name = tk.StringVar()
        self.v_age = tk.StringVar(value="30")
        self.v_gender = tk.StringVar(value=GENDERS[0])
        self.v_grade = tk.StringVar(value="C")
        self.v_partner = tk.StringVar()

        # 라벨을 위, 입력칸을 아래에 두는 세로 묶음
        row = ttk.Frame(box)
        row.pack(fill="x")
        self.partner_combo = ttk.Combobox(row, textvariable=self.v_partner, width=9, state="readonly")
        name = ttk.Entry(row, textvariable=self.v_name, width=11)
        name.bind("<Return>", lambda e: self._add())
        fields = [
            ("이름", name),
            ("나이", ttk.Spinbox(row, from_=5, to=99, textvariable=self.v_age, width=4)),
            ("성별", ttk.Combobox(row, values=GENDERS, textvariable=self.v_gender, width=3,
                                state="readonly")),
            ("급수", ttk.Combobox(row, values=GRADES, textvariable=self.v_grade, width=4,
                                state="readonly")),
            ("고정 파트너", self.partner_combo),
        ]
        for c, (label, widget) in enumerate(fields):
            ttk.Label(row, text=label, style="Muted.TLabel").grid(row=0, column=c, sticky="w", padx=(0, 10))
            widget.grid(row=1, column=c, sticky="w", padx=(0, 10), pady=(2, 0))

        row = ttk.Frame(box)
        row.pack(fill="x", pady=(12, 0))
        self.add_btn = ttk.Button(row, text="➕ 추가", style="Accent.TButton", command=self._add)
        self.add_btn.pack(side="left")
        self.edit_btn = ttk.Button(row, text="✎ 수정 저장", command=self._update, state="disabled")
        self.edit_btn.pack(side="left", padx=6)
        ttk.Button(row, text="새로 입력", command=self._reset_form).pack(side="left")

    def _build_settings(self, parent):
        box, _ = self._card(parent, "대진 설정", side="bottom")
        self.v_courts = tk.StringVar(value="3")
        self.v_games = tk.StringVar(value="4")
        self.v_mode = tk.StringVar(value=MODE_MIXED)
        self.v_cross = tk.BooleanVar(value=False)
        self.v_event = tk.StringVar(value="30")
        self.v_time = tk.StringVar(value="5")

        grid = ttk.Frame(box)
        grid.pack(fill="x")
        items = [
            ("코트 수", ttk.Spinbox(grid, from_=1, to=30, textvariable=self.v_courts, width=5)),
            ("1인당 게임 수", ttk.Spinbox(grid, from_=1, to=30, textvariable=self.v_games, width=5)),
            ("성별 모드", ttk.Combobox(grid, values=GENDER_MODES, textvariable=self.v_mode,
                                    width=13, state="readonly")),
            ("이벤트 게임 비율(%)", ttk.Spinbox(grid, from_=0, to=100, increment=10,
                                         textvariable=self.v_event, width=5)),
            ("탐색 시간(초)", ttk.Spinbox(grid, from_=1, to=120, textvariable=self.v_time, width=5)),
        ]
        for i, (label, widget) in enumerate(items):
            r, c = divmod(i, 2)
            ttk.Label(grid, text=label, style="Muted.TLabel").grid(
                row=r, column=c * 2, sticky="w", padx=(0, 6), pady=3)
            widget.grid(row=r, column=c * 2 + 1, sticky="w", padx=(0, 20), pady=3)
        self.cross_check = ttk.Checkbutton(grid, text="성별 구성이 다른 대결 허용 (예: 남남 vs 남녀)",
                                           variable=self.v_cross)
        self.cross_check.grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))

        self.preview = ttk.Label(box, style="Info.TLabel")
        self.preview.pack(fill="x", pady=(8, 0))
        for v in (self.v_courts, self.v_games, self.v_mode):
            v.trace_add("write", lambda *a: self._update_preview())

        row = ttk.Frame(box)
        row.pack(fill="x", pady=(12, 0))
        self.gen_btn = ttk.Button(row, text="🏸 대진표 생성", style="Big.Accent.TButton",
                                  command=self._generate)
        self.gen_btn.pack(side="left")
        self.progress = ttk.Progressbar(row, mode="determinate", maximum=100,
                                        style="Lime.Horizontal.TProgressbar")
        self.progress.pack(side="left", fill="x", expand=True, padx=(14, 0))

    def _build_results(self, parent):
        box, _ = self._card(parent, "결과", expand=True)
        bar = ttk.Frame(box)
        bar.pack(side="bottom", fill="x", pady=(12, 0))
        self.status = ttk.Label(bar, text="명단을 불러오거나 참가자를 추가한 뒤 대진표를 생성해주세요",
                                style="Status.TLabel")
        self.status.pack(side="left")
        self.save_btn = ttk.Button(bar, text="📊 엑셀로 저장", style="Accent.TButton",
                                   command=self._save_schedule, state="disabled")
        self.save_btn.pack(side="right")

        self.tabs = ttk.Notebook(box)
        self.tabs.pack(fill="both", expand=True)

        tab = ttk.Frame(self.tabs, padding=(0, 8, 0, 0))
        self.tabs.add(tab, text="대진표")
        frame, self.games_tree = self._tree(
            tab, ("라운드", "코트", "구분", "종류", "팀 A", "vs", "팀 B"),
            (80, 50, 75, 75, 210, 40, 210), height=10)
        self.games_tree.configure(show="headings")
        self.games_tree.tag_configure("event", background=ROW_EVENT)
        self.games_tree.tag_configure("review", background=ROW_REVIEW, foreground=REVIEW_TEXT)
        self.games_tree.tag_configure("round", background=ROW_ROUND, foreground=LIME_DEEP,
                                      font=(self.family, 10, "bold"))
        frame.pack(fill="both", expand=True)
        legend = ttk.Frame(tab)
        legend.pack(fill="x", pady=(6, 0))
        for color, text in ((ROW_ROUND, "라운드 · 휴식 명단"), (ROW_EVENT, "이벤트 게임"),
                            (ROW_REVIEW, "검토 필요 (양 팀 실력 차이 큼)")):
            tk.Frame(legend, bg=color, width=14, height=14, highlightthickness=1,
                     highlightbackground=BORDER).pack(side="left", padx=(0, 5))
            ttk.Label(legend, text=text, style="Muted.TLabel").pack(side="left", padx=(0, 14))

        tab = ttk.Frame(self.tabs, padding=(0, 8, 0, 0))
        self.tabs.add(tab, text="인원별 통계")
        cols = ("이름", "성별", "나이", "급수", "총 게임", "경기 패턴", "휴식",
                "최대 연속", "3연속+", "같은 급수", "이벤트", "남복", "여복", "혼복", "혼성",
                "파트너 중복", "상대 중복")
        widths = (70, 45, 45, 45, 60, 110, 45, 75, 60, 75, 60, 45, 45, 45, 45, 85, 75)
        frame, self.stats_tree = self._tree(tab, cols, widths, height=10)
        self.stats_tree.tag_configure("extra", background=ROW_EXTRA)
        frame.pack(fill="both", expand=True)
        ttk.Label(tab, text="경기 패턴: ● 경기 / ○ 휴식 (왼쪽부터 1라운드) · 노란 줄: 한 게임 더 하는 사람",
                  style="Muted.TLabel").pack(anchor="w", pady=(6, 0))

        tab = ttk.Frame(self.tabs, padding=(0, 8, 0, 0))
        self.tabs.add(tab, text="요약")
        frame, self.summary_tree = self._tree(tab, ("항목", "값"), (260, 260), height=10)
        self.summary_tree.tag_configure("review", background=ROW_REVIEW, foreground=REVIEW_TEXT)
        frame.pack(fill="both", expand=True)

    # ----- 명단 ---------------------------------------------------------

    def _refresh_roster(self):
        self.roster.delete(*self.roster.get_children())
        for i, p in enumerate(self.players):
            self.roster.insert("", "end", iid=str(i),
                               values=(p.name, p.age, p.gender, p.grade, p.partner),
                               tags=("stripe",) if i % 2 else ())
        men = sum(p.gender == "남" for p in self.players)
        self.roster_count.configure(
            text=f"{len(self.players)}명 · 남 {men} / 여 {len(self.players) - men}")
        self.partner_combo.configure(values=[""] + [p.name for p in self.players])
        self._update_preview()

    def _form_player(self):
        return make_player(self.v_name.get(), self.v_age.get(), self.v_gender.get(),
                           self.v_grade.get(), self.v_partner.get())

    def _set_partner(self, idx, partner_name):
        """고정 파트너는 서로 연결해요 (이전 짝은 풀어요)."""
        p = self.players[idx]
        for q in self.players:
            if q is not p and (q.partner == p.name or (partner_name and q.partner == partner_name)):
                q.partner = ""
        p.partner = partner_name
        if partner_name:
            for q in self.players:
                if q.name == partner_name:
                    if q.partner and q.partner != p.name:
                        for x in self.players:
                            if x.name == q.partner:
                                x.partner = ""
                    q.partner = p.name

    def _add(self):
        try:
            p = self._form_player()
        except ValueError as e:
            messagebox.showwarning(TITLE, str(e))
            return
        if any(q.name == p.name for q in self.players):
            messagebox.showwarning(TITLE, f"'{p.name}'은(는) 이미 명단에 있어요")
            return
        partner = p.partner
        p.partner = ""
        self.players.append(p)
        self._set_partner(len(self.players) - 1, partner)
        self._refresh_roster()
        self._reset_form()

    def _update(self):
        if self.editing is None:
            return
        try:
            p = self._form_player()
        except ValueError as e:
            messagebox.showwarning(TITLE, str(e))
            return
        old = self.players[self.editing]
        if p.name != old.name and any(q.name == p.name for q in self.players):
            messagebox.showwarning(TITLE, f"'{p.name}'은(는) 이미 명단에 있어요")
            return
        for q in self.players:  # 이름이 바뀌면 파트너 이름도 같이
            if q.partner == old.name:
                q.partner = p.name
        old.name, old.age, old.gender, old.grade = p.name, p.age, p.gender, p.grade
        self._set_partner(self.editing, p.partner if p.partner != p.name else "")
        self._refresh_roster()
        self._reset_form()

    def _on_select(self, _event=None):
        sel = self.roster.selection()
        if len(sel) != 1:
            return
        self.editing = int(sel[0])
        p = self.players[self.editing]
        self.v_name.set(p.name)
        self.v_age.set(str(p.age))
        self.v_gender.set(p.gender)
        self.v_grade.set(p.grade)
        self.v_partner.set(p.partner)
        self.edit_btn.configure(state="normal")

    def _reset_form(self):
        self.editing = None
        self.v_name.set("")
        self.v_partner.set("")
        self.edit_btn.configure(state="disabled")
        self.roster.selection_remove(self.roster.selection())

    def _delete(self):
        sel = sorted((int(i) for i in self.roster.selection()), reverse=True)
        if not sel:
            return
        for i in sel:
            name = self.players[i].name
            del self.players[i]
            for q in self.players:
                if q.partner == name:
                    q.partner = ""
        self._reset_form()
        self._refresh_roster()

    def _clear(self):
        if self.players and messagebox.askyesno(TITLE, "명단을 모두 지울까요?"):
            self.players = []
            self._reset_form()
            self._refresh_roster()

    def _load(self):
        path = filedialog.askopenfilename(
            title="명단 불러오기",
            filetypes=[("엑셀/CSV", "*.xlsx *.xlsm *.csv"), ("모든 파일", "*.*")])
        if not path:
            return
        try:
            players = load_players(path)
        except Exception as e:
            messagebox.showerror(TITLE, f"명단을 읽지 못했어요\n\n{e}")
            return
        if self.players and not messagebox.askyesno(
                TITLE, "지금 명단을 바꿀까요?\n('아니오'를 누르면 지금 명단 뒤에 추가해요)"):
            names = {p.name for p in self.players}
            players = self.players + [p for p in players if p.name not in names]
        self.players = players
        self._reset_form()
        self._refresh_roster()
        _, errors = find_pairs(self.players)
        if errors:
            messagebox.showwarning(TITLE, "고정 파트너를 확인해주세요\n\n" + "\n".join(errors))

    def _save_roster(self):
        if not self.players:
            return
        path = filedialog.asksaveasfilename(
            title="명단 저장", defaultextension=".xlsx",
            filetypes=[("엑셀", "*.xlsx"), ("CSV", "*.csv")], initialfile="참가자명단.xlsx")
        if path:
            try:
                save_players(self.players, path)
                self.status.configure(text=f"명단을 저장했어요: {path}")
            except Exception as e:
                messagebox.showerror(TITLE, f"저장하지 못했어요\n\n{e}")

    # ----- 설정 / 생성 -----------------------------------------------------

    def _read_settings(self):
        try:
            courts = int(self.v_courts.get())
            games = int(self.v_games.get())
            event = float(self.v_event.get())
            limit = float(self.v_time.get())
        except ValueError:
            raise ValueError("설정 값은 숫자로 입력해주세요")
        if not 0 <= event <= 100:
            raise ValueError("이벤트 게임 비율은 0~100% 사이로 입력해주세요")
        return Settings(courts, games, self.v_mode.get(), self.v_cross.get(), event / 100, limit)

    def _update_preview(self):
        free = self.v_mode.get() == MODE_FREE
        self.cross_check.configure(state="disabled" if free else "normal")
        n = len(self.players)
        try:
            courts, games = int(self.v_courts.get()), int(self.v_games.get())
        except ValueError:
            self.preview.configure(text="")
            return
        used = min(courts, n // 4)
        if used < 1 or games < 1:
            self.preview.configure(text="예상: 4명 이상 필요해요")
            return
        rounds = math.ceil(n * games / (used * 4))
        extra = rounds * used * 4 - n * games
        text = f"예상: 코트 {used}면 · {rounds}라운드 · {rounds * used}게임"
        text += f" · {extra}명은 {games + 1}게임" if extra else f" · 모두 {games}게임"
        if used < courts:
            text += f"  (인원이 부족해서 코트 {used}면만 써요)"
        self.preview.configure(text=text)

    def _generate(self):
        if self._job:
            return
        try:
            settings = self._read_settings()
        except ValueError as e:
            messagebox.showwarning(TITLE, str(e))
            return
        if len(self.players) < 4:
            messagebox.showwarning(TITLE, "4명 이상 있어야 복식 게임을 짤 수 있어요")
            return

        # 생성 중에 명단을 고쳐도 결과가 섞이지 않게 복사해서 넘겨요
        players = [copy.copy(p) for p in self.players]
        job = {"progress": 0.0, "result": None, "error": None, "done": False}
        self._job = job

        def work():
            try:
                job["result"] = generate(players, settings,
                                         progress=lambda f: job.__setitem__("progress", f))
            except ScheduleError as e:
                job["error"] = str(e)
            except Exception as e:  # 예상 못 한 오류도 화면에 알려줘요
                job["error"] = f"{type(e).__name__}: {e}"
            job["done"] = True

        self.gen_btn.configure(state="disabled")
        self.status.configure(text="대진표를 짜는 중이에요...")
        threading.Thread(target=work, daemon=True).start()
        self._poll()

    def _poll(self):
        job = self._job
        self.progress["value"] = job["progress"] * 100
        if not job["done"]:
            self.root.after(100, self._poll)
            return
        self._job = None
        self.gen_btn.configure(state="normal")
        self.progress["value"] = 0
        if job["error"]:
            self.status.configure(text="대진표를 만들지 못했어요 😢")
            messagebox.showerror(TITLE, job["error"])
            return
        self.schedule = job["result"]
        self._show_schedule()
        self.save_btn.configure(state="normal")
        self.status.configure(text="대진표 완성! 🎉 엑셀로 저장할 수 있어요")

    # ----- 결과 ---------------------------------------------------------

    def _show_schedule(self):
        s = self.schedule
        t = self.games_tree
        t.delete(*t.get_children())
        for games, resting in zip(s.rounds, s.resting):
            # 휴식 명단은 팀 A / 팀 B 칸에 3명씩 나눠서 보여줘요
            names = [p.name for p in resting]
            chunks = [", ".join(names[i:i + 3]) for i in range(0, len(names), 3)] or ["없음"]
            for i in range(0, len(chunks), 2):
                first = i == 0
                t.insert("", "end", values=(
                    f"{games[0].round}라운드" if first else "", "", "",
                    f"휴식 {len(names)}명" if first else "",
                    chunks[i], "", chunks[i + 1] if i + 1 < len(chunks) else ""),
                    tags=("round",))
            for gm in games:
                tag = "review" if gm.needs_review else ("event" if gm.kind == KIND_EVENT else "")
                t.insert("", "end", values=("", gm.court, gm.kind, gm.gender_type,
                                            team_text(gm.team_a), "vs", team_text(gm.team_b)),
                         tags=(tag,) if tag else ())

        t = self.stats_tree
        t.delete(*t.get_children())
        base = s.settings.games_per_player
        for i, st in enumerate(sorted(s.stats, key=lambda x: (-x.player.level, x.player.name))):
            p = st.player
            tag = "extra" if st.games > base else ("stripe" if i % 2 else "")
            t.insert("", "end", values=(
                p.name, p.gender, p.age, p.grade, st.games, st.pattern, st.rests,
                st.max_streak, st.three_in_row, st.same_grade, st.event, st.men_doubles,
                st.women_doubles, st.mixed_doubles, st.cross, st.partner_repeats,
                st.opponent_repeats), tags=(tag,) if tag else ())

        t = self.summary_tree
        t.delete(*t.get_children())
        for i, (k, v) in enumerate(s.summary.items()):
            t.insert("", "end", values=(k, v), tags=("stripe",) if i % 2 else ())
            if k.startswith("검토 필요"):  # 검토 필요 경기 목록을 바로 아래에
                for label, text in review_rows(s):
                    t.insert("", "end", values=(label, text), tags=("review",))
        self.tabs.select(0)

    def _save_schedule(self):
        if not self.schedule:
            return
        path = filedialog.asksaveasfilename(
            title="대진표 저장", defaultextension=".xlsx",
            filetypes=[("엑셀", "*.xlsx")], initialfile="배드민턴_대진표.xlsx")
        if not path:
            return
        try:
            save_schedule(self.schedule, path)
            self.status.configure(text=f"엑셀로 저장했어요: {path}")
        except PermissionError:
            messagebox.showerror(TITLE, "파일이 열려 있어서 저장하지 못했어요. 엑셀을 닫고 다시 시도해주세요")
        except Exception as e:
            messagebox.showerror(TITLE, f"저장하지 못했어요\n\n{e}")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
