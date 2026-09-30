"""淺色石板藍風格（參考 chilljudge.com 淺色主題的美感：淺灰藍底、白色細框卡片、深色膠囊主按鈕、等寬小標籤）。"""

from PyQt6.QtGui import QColor, QFont

# ── 色票（Tailwind slate 系，淺色） ──
BG = '#f1f5f9'          # 頁面
CARD = '#ffffff'        # 卡片
ELEVATED = '#f8fafc'    # 卡片內的區塊
BORDER = '#e2e8f0'
BORDER_STRONG = '#cad5e2'
TERTIARY = '#90a1b9'    # 停用文字
SECONDARY = '#62748e'   # 次要文字
LABEL = '#314158'       # 主要文字
STRONG = '#1d293d'      # 標題、大數字、主按鈕
PAPER = '#f8fafc'       # 筆跡面板
PAPER_LINE = '#e2e8f0'
INK = '#2b3a4d'
VIDEO_BG = '#0f172b'    # 鏡頭畫面四周（深色螢幕）

TEAL = '#009689'        # 成功（文字用）
TEAL_BRIGHT = '#00bba7' # 書寫中筆跡、下筆指尖
YELLOW = '#bb4d00'      # 警告（文字用，琥珀）
YELLOW_BRIGHT = '#fdc700'  # 握拳進度環
ROSE = '#ec003f'        # 錯誤／取消
GOOD = '#10a85e'

# 舊名稱（其他模組沿用）
FILL = ELEVATED
SEPARATOR = BORDER
GREEN = TEAL
ORANGE = YELLOW
RED = ROSE
BLUE = LABEL
INDIGO = '#4f39f6'

TONE = {'neutral': SECONDARY, 'active': STRONG, 'ok': TEAL, 'warn': YELLOW, 'error': ROSE}

FONT_FAMILIES = ['Noto Sans CJK TC', 'Noto Sans TC', 'PingFang TC', 'Microsoft JhengHei UI',
                 'Microsoft JhengHei', 'sans-serif']
MONO_FAMILIES = ['Fira Code', 'Ubuntu Sans Mono', 'Ubuntu Mono', 'DejaVu Sans Mono', 'Noto Sans Mono CJK TC',
                 'Consolas', 'monospace']


def font(size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont()
    f.setFamilies(FONT_FAMILIES)
    f.setPixelSize(size)
    f.setWeight(weight)
    return f


def mono(size: int, weight: QFont.Weight = QFont.Weight.Medium) -> QFont:
    f = QFont()
    f.setFamilies(MONO_FAMILIES)
    f.setPixelSize(size)
    f.setWeight(weight)
    return f


def qc(hex_color: str, alpha: int = 255) -> QColor:
    c = QColor(hex_color)
    c.setAlpha(alpha)
    return c


_MONO = ', '.join(f"'{f}'" for f in MONO_FAMILIES[:-1]) + ', monospace'

STYLESHEET = f"""
QMainWindow, QWidget#page {{ background: {BG}; }}
QScrollArea {{ background: {BG}; border: none; }}
QScrollArea > QWidget > QWidget {{ background: {BG}; }}
QWidget {{ color: {LABEL}; }}
QFrame#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 22px; }}
QFrame#nav {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 24px; }}
QLabel {{ color: {LABEL}; background: transparent; border: none; }}
QLabel#wordmark {{ color: {SECONDARY}; font-family: {_MONO}; font-size: 15px; font-weight: 600; }}
QLabel#navTitle {{ color: {STRONG}; font-size: 17px; font-weight: 700; }}
QLabel#navSep {{ color: {BORDER_STRONG}; font-size: 16px; }}
QLabel#section {{ color: {SECONDARY}; font-family: {_MONO}; font-size: 12px; font-weight: 600;
                  letter-spacing: 1px; }}
QLabel#headline {{ color: {STRONG}; font-size: 22px; font-weight: 700; letter-spacing: -0.5px; }}
QLabel#detail {{ color: {SECONDARY}; font-size: 14px; }}
QLabel#bigNumber {{ font-size: 76px; font-weight: 700; color: {STRONG}; letter-spacing: -3px; }}
QLabel#caption {{ color: {SECONDARY}; font-size: 12px; }}
QLabel#monoCaption {{ color: {SECONDARY}; font-family: {_MONO}; font-size: 12px; }}
QLabel#progressText {{ color: {STRONG}; font-family: {_MONO}; font-size: 32px; font-weight: 600; }}
QLabel#pill {{ border-radius: 7px; padding: 3px 9px; font-family: {_MONO}; font-size: 12px; font-weight: 600;
               letter-spacing: 0.5px; }}
QLabel#simBanner {{ background: #fffbeb; color: {YELLOW}; border: 1px solid #fee685;
                    border-radius: 12px; padding: 7px 12px; font-size: 13px; font-weight: 600; }}

QPushButton {{ background: {CARD}; color: {LABEL}; border: 1px solid {BORDER_STRONG}; border-radius: 22px;
               font-size: 15px; font-weight: 600; padding: 0 18px; min-height: 44px; max-height: 44px;
               letter-spacing: 0.4px; }}
QPushButton:hover {{ background: {ELEVATED}; border-color: {TERTIARY}; }}
QPushButton:pressed {{ background: {BG}; }}
QPushButton:disabled {{ background: transparent; color: {TERTIARY}; border-color: {BORDER}; }}
QPushButton#primary {{ background: {STRONG}; color: {ELEVATED}; border: 1px solid {STRONG}; border-radius: 26px;
                       font-size: 17px; min-height: 52px; max-height: 52px; }}
QPushButton#primary:hover {{ background: {LABEL}; border-color: {LABEL}; }}
QPushButton#primary:pressed {{ background: #0f172b; border-color: #0f172b; }}
QPushButton#primary:disabled {{ background: {BORDER}; color: {TERTIARY}; border-color: {BORDER}; }}
QPushButton#danger {{ background: #fff1f2; color: {ROSE}; border: 1px solid #ffccd3; }}
QPushButton#danger:hover {{ background: #ffe4e6; border-color: #ffa1ad; }}
QPushButton#danger:disabled {{ background: transparent; color: {TERTIARY}; border-color: {BORDER}; }}
QPushButton#connect {{ background: #f0fdfa; color: #00786f; border: 1px solid #96f7e4; }}
QPushButton#connect:hover {{ background: #cbfbf1; }}
QPushButton#connect:disabled {{ background: transparent; color: {TERTIARY}; border-color: {BORDER}; }}
QPushButton#plain {{ background: transparent; color: {SECONDARY}; border: 1px solid transparent; font-size: 14px; }}
QPushButton#plain:hover {{ color: {LABEL}; background: {CARD}; border-color: {BORDER}; }}
QPushButton#plain:disabled {{ color: {TERTIARY}; }}

QMessageBox {{ background: {CARD}; }}
QMessageBox QLabel {{ color: {LABEL}; font-size: 14px; }}
QMessageBox QPushButton {{ min-width: 96px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER_STRONG}; border-radius: 3px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QToolTip {{ background: {CARD}; color: {LABEL}; border: 1px solid {BORDER_STRONG}; padding: 6px; }}
"""
