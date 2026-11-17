"""检查更新与更新日志：右上角两个按钮，加上启动时的两件事。

  · 升级后第一次打开：弹出这次更新了什么（上次打开的版本 → 当前版本之间的每一节）
  · 启动后静默检查一次新版本：有就提示，没有或断网都不打扰

同样是挂在主窗口上的 mixin——它要用主窗口的 settings 和顶栏。
"""

import threading

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QMessageBox, QPushButton

from logger import print_log
from updater import (
    check_for_update, full_changelog_markdown, notes_between, read_changelog, section,
)
from ui.dialogs.notes import DOWNLOAD, SKIP, NotesDialog
from version import __version__

STARTUP_CHECK_DELAY_MS = 3000   # 窗口先出来、"这次更新了什么"先看完，再去联网


class _CheckResult(QObject):
    """后台线程把结果送回界面线程用的信号；跨线程 emit 会自动排队到主线程。"""

    done = pyqtSignal(object, object, bool)   # update(dict|None), error(Exception|None), silent


class UpdateMixin:
    """需要宿主提供：settings、_first_run。"""

    def _build_update_buttons(self):
        """顶栏右边的「更新日志」「检查更新」，返回按钮列表由顶栏去摆。"""
        self.changelog_btn = QPushButton("更新日志")
        self.changelog_btn.setObjectName("secondary")
        self.changelog_btn.setToolTip("看看每个版本都改了什么")
        self.changelog_btn.clicked.connect(self.show_changelog)

        self.update_btn = QPushButton("检查更新")
        self.update_btn.setObjectName("secondary")
        self.update_btn.setToolTip(f"当前版本 v{__version__}")
        self.update_btn.clicked.connect(lambda: self.check_updates(silent=False))

        self._update_result = _CheckResult()
        self._update_result.done.connect(self._on_update_checked)
        self._checking_update = False
        return [self.changelog_btn, self.update_btn]

    def _start_update_flow(self):
        """窗口显示之后调：先「这次更新了什么」，过一会再静默检查新版本。"""
        QTimer.singleShot(0, self._show_whats_new)
        QTimer.singleShot(STARTUP_CHECK_DELAY_MS, lambda: self.check_updates(silent=True))

    # ------------------------------------------------------------------ #
    # 更新日志
    # ------------------------------------------------------------------ #

    def show_changelog(self):
        NotesDialog(
            "更新日志", full_changelog_markdown(read_changelog()),
            intro=f"当前版本 v{__version__}", parent=self,
        ).exec()

    def _show_whats_new(self):
        last = self.settings.value("last_seen_version")
        self.settings.setValue("last_seen_version", __version__)
        if last == __version__:
            return
        if last is None and self._first_run:
            # 头一次装的人不需要知道"更新了什么"。
            # 只看 _first_run 不够：没点过「保存」的人方案从没落盘，每次启动都像首次运行
            return

        text = read_changelog()
        # 从还没有这个功能的老版本升上来时不知道原来是哪版，只给看当前这一版
        notes = notes_between(text, last, __version__) if last else section(text, __version__)
        if not notes:
            return
        NotesDialog(
            f"已更新到 v{__version__}", notes,
            intro="这次更新的内容如下。以后想再看，点右上角「更新日志」。", parent=self,
        ).exec()

    # ------------------------------------------------------------------ #
    # 检查新版本
    # ------------------------------------------------------------------ #

    def check_updates(self, silent):
        """silent：启动时的自动检查，没有新版本、出错、被跳过的版本都不弹窗。"""
        if self._checking_update:
            return
        self._checking_update = True
        if not silent:
            self.update_btn.setEnabled(False)
            self.update_btn.setText("检查中")

        def work():
            try:
                self._update_result.done.emit(check_for_update(), None, silent)
            except Exception as error:   # 断网、超时、GitHub 限流……都交给界面线程决定怎么说
                self._update_result.done.emit(None, error, silent)

        threading.Thread(target=work, daemon=True).start()

    def _on_update_checked(self, update, error, silent):
        self._checking_update = False
        self.update_btn.setEnabled(True)

        if error is not None:
            self.update_btn.setText("检查更新")
            print_log(f"⚠️ 检查更新失败：{error}")
            if not silent:
                QMessageBox.warning(self, "检查更新", f"连不上更新服务器，稍后再试。\n\n{error}")
            return

        if update is None:
            self.update_btn.setText("检查更新")
            if not silent:
                QMessageBox.information(self, "检查更新", f"已经是最新版本（v{__version__}）。")
            return

        # 有新版本：按钮一直亮着提示，就算这次选了「稍后」，之后也能点它再看
        self.update_btn.setText(f"新版本 v{update['version']}")
        self.update_btn.setObjectName("")
        self.update_btn.style().unpolish(self.update_btn)
        self.update_btn.style().polish(self.update_btn)
        print_log(f"v{update['version']}（当前 v{__version__}）", event="⬆️ 有新版本", simple=True)

        if silent and self.settings.value("skipped_update_version") == update["version"]:
            return
        dialog = NotesDialog(
            f"有新版本 v{update['version']}", update["notes"],
            intro=f"当前版本 v{__version__}。下载安装后，配置方案和接收历史都会保留。",
            update=True, parent=self,
        )
        dialog.exec()
        if dialog.action == DOWNLOAD:
            QDesktopServices.openUrl(QUrl(update["download_url"]))
        elif dialog.action == SKIP:
            self.settings.setValue("skipped_update_version", update["version"])
