"""配置方案：一个仓库一套配置，可命名、切换、设默认。

以 mixin 的形式挂在主窗口上——这些方法要读写各张卡片上的控件，
拆成独立组件反而要来回传一堆引用。
"""

import json

from PyQt6.QtWidgets import QInputDialog, QMessageBox

from logger import print_log
from printer_utils import (
    DEFAULT_PRINTER_KEY, SYSTEM_PRINTER, get_available_printers, make_printer,
)
from ui.constants import (
    CONCURRENT, DATA_ADDR, DEFAULT_PROFILE_NAME, MAX_PROFILES, ONEWAY, SERIAL, TWOWAY,
)
from ui.helpers import clamp_timeout, normalize_addr, sanitize_printers


class ProfileMixin:
    """需要宿主提供：settings、各卡片控件、printer_list。"""

    # ------------------------------------------------------------------ #
    # 配置方案：一个仓库一套（端口 / 打印机 / 数据地址 / 功能开关）
    # ------------------------------------------------------------------ #

    def _load_profiles(self):
        """从 QSettings 读回方案列表；损坏或为空时用当前默认值造一条。"""
        profiles = []
        raw = self.settings.value("profiles")
        try:
            items = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            print_log("⚠️ 配置方案读取失败，已重置为默认方案")
            items = []

        names = set()
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name or name in names:
                continue
            names.add(name)
            profiles.append(self._sanitize_profile(name, item))

        if not profiles:
            # 首次运行：把上一版本记住的地址带过来，不让用户重填
            addr = normalize_addr(self.settings.value("data_addr")) or DATA_ADDR
            # 首次运行顺手把系统默认队列配成一台打印机，省得开箱是空表
            queues, default_queue = get_available_printers()
            seed = [make_printer(DEFAULT_PRINTER_KEY, "默认", SYSTEM_PRINTER,
                                 queue=default_queue or (queues[0] if queues else ""))]
            profiles.append(self._sanitize_profile(DEFAULT_PROFILE_NAME, {
                "ws_port": "8765",
                "data_addr": addr,
                "printers": seed if (queues or default_queue) else [],
            }))
        return profiles[:MAX_PROFILES]

    @staticmethod
    def _sanitize_profile(name, item):
        """只保留认识的字段，缺项补默认值，避免手改过的 json 把界面带崩。"""
        return {
            "name": name,
            "ws_port": str(item.get("ws_port", "8765")).strip() or "8765",
            "printers": sanitize_printers(item),
            "data_addr": normalize_addr(item.get("data_addr")) or DATA_ADDR,
            "enable_print": bool(item.get("enable_print", False)),
            "enable_data": bool(item.get("enable_data", True)),
            "flow_mode": item.get("flow_mode") if item.get("flow_mode") in (ONEWAY, TWOWAY) else ONEWAY,
            "reply_timeout": clamp_timeout(item.get("reply_timeout")),
            "dispatch_mode": (
                item.get("dispatch_mode")
                if item.get("dispatch_mode") in (CONCURRENT, SERIAL) else CONCURRENT
            ),
        }

    def _persist_profiles(self):
        self.settings.setValue("profiles", json.dumps(self.profiles, ensure_ascii=False))
        self.settings.setValue("default_profile", self.default_profile)
        self.settings.setValue("current_profile", self.current_profile)

    def _profile_index(self, name):
        for i, profile in enumerate(self.profiles):
            if profile["name"] == name:
                return i
        return -1

    def _current_config(self):
        """界面上此刻的配置，用于保存 / 与已存方案比对。"""
        return {
            "ws_port": self.port_input.text().strip(),
            "printers": [dict(p) for p in self.printer_list.printers],
            "data_addr": normalize_addr(self.addr_input.text()) or self.addr_input.text().strip(),
            "enable_print": self.print_checkbox.isChecked(),
            "enable_data": self.data_checkbox.isChecked(),
            "flow_mode": self.flow_combo.currentData(),
            "reply_timeout": clamp_timeout(self.timeout_input.text().strip()),
            "dispatch_mode": self.dispatch_combo.currentData(),
        }

    def _apply_profile(self, profile):
        """把方案写回各个输入框；期间屏蔽 dirty 检测，避免自己把自己标成已修改。"""
        self._applying_profile = True
        try:
            self.port_input.setText(profile["ws_port"])
            self.addr_input.setText(profile["data_addr"])
            self.print_checkbox.setChecked(profile["enable_print"])
            self.data_checkbox.setChecked(profile["enable_data"])
            self.flow_combo.setCurrentIndex(
                max(self.flow_combo.findData(profile["flow_mode"]), 0)
            )
            self.dispatch_combo.setCurrentIndex(
                max(self.dispatch_combo.findData(profile["dispatch_mode"]), 0)
            )
            self.timeout_input.setText(f"{profile['reply_timeout']:g}")

            self.printer_list.set_printers(profile["printers"])

            # 换了台机器 / 打印机没开：不静默改掉配置，只提示，启动时会拦下来
            available, _ = get_available_printers()
            for printer in self.printer_list.printers:
                if printer["kind"] == SYSTEM_PRINTER and printer["queue"] not in available:
                    print_log(
                        f"⚠️ 方案「{profile['name']}」的打印机 {printer['key']} "
                        f"用的队列 {printer['queue']} 当前不可用"
                    )
        finally:
            self._applying_profile = False

        self.current_profile = profile["name"]
        self.toggle_modules()
        # 以"实际落到界面上的值"为基准判断改没改过：方案里的打印机在本机不存在时，
        # 界面上仍是原来那台，这不该被当成用户的未保存修改。
        self._applied_config = self._current_config()
        self._update_profile_hint()

    def _refresh_profile_combo(self):
        """重建下拉项；默认方案加 ★ 前缀，item 的 data 里放真名。"""
        self._applying_profile = True
        try:
            self.profile_combo.clear()
            for profile in self.profiles:
                name = profile["name"]
                label = f"★ {name}" if name == self.default_profile else name
                self.profile_combo.addItem(label, name)
            index = self._profile_index(self.current_profile)
            self.profile_combo.setCurrentIndex(max(index, 0))
        finally:
            self._applying_profile = False

    def _is_dirty(self):
        return self._applied_config is not None and self._applied_config != self._current_config()

    def _update_profile_hint(self):
        if self._applying_profile or not self._ui_ready:
            return
        if self._profile_index(self.current_profile) < 0:
            self.profile_hint.setText("★ 为默认方案，启动程序时自动载入")
            return

        star = "★ " if self.current_profile == self.default_profile else ""
        self.profile_hint.setText(
            f"{star}{self.current_profile}"
            + ("　·　有未保存的修改" if self._is_dirty() else "　·　已保存")
        )

    def on_profile_selected(self, index):
        """下拉切换：一键把这套仓库的端口 / 打印机 / 地址全换掉。"""
        if self._applying_profile or index < 0:
            return

        name = self.profile_combo.itemData(index)
        target = self._profile_index(name)
        if target < 0 or name == self.current_profile:
            return

        # 有未保存的改动就问一句，避免切走后改的东西凭空消失
        if self._is_dirty():
            answer = QMessageBox.question(
                self, "切换方案",
                f"方案「{self.current_profile}」有未保存的修改，切换后会丢弃。继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._refresh_profile_combo()   # 选回原来那条
                return

        self._apply_profile(self.profiles[target])
        self.settings.setValue("current_profile", self.current_profile)
        print_log(f"🔀 已切换到方案「{self.current_profile}」")

    def save_profile(self):
        """把界面上的配置写回当前方案。"""
        index = self._profile_index(self.current_profile)
        if index < 0:
            self.new_profile()
            return
        if self._validate_addr() is None:
            return

        self.profiles[index] = self._sanitize_profile(
            self.current_profile, self._current_config()
        )
        self._persist_profiles()
        self._apply_profile(self.profiles[index])   # 回填规整后的地址
        print_log(f"💾 已保存方案「{self.current_profile}」")

    def new_profile(self):
        """用当前界面配置另存为一个新方案（例如「仓库A」）。"""
        if len(self.profiles) >= MAX_PROFILES:
            QMessageBox.warning(
                self, "错误", f"最多保存 {MAX_PROFILES} 套方案，请先删除不用的"
            )
            return
        if self._validate_addr() is None:
            return

        name, ok = QInputDialog.getText(
            self, "新建方案", "方案名称（例如 仓库A）:", text=""
        )
        name = name.strip()
        if not ok or not name:
            return
        if self._profile_index(name) >= 0:
            QMessageBox.warning(self, "错误", f"方案「{name}」已存在")
            return

        self.profiles.append(self._sanitize_profile(name, self._current_config()))
        self.current_profile = name
        self._persist_profiles()
        self._refresh_profile_combo()
        self._apply_profile(self.profiles[-1])
        print_log(f"➕ 已新建方案「{name}」")

    def rename_profile(self):
        index = self._profile_index(self.current_profile)
        if index < 0:
            return

        name, ok = QInputDialog.getText(
            self, "重命名方案", "新的方案名称:", text=self.current_profile
        )
        name = name.strip()
        if not ok or not name or name == self.current_profile:
            return
        if self._profile_index(name) >= 0:
            QMessageBox.warning(self, "错误", f"方案「{name}」已存在")
            return

        old = self.current_profile
        self.profiles[index]["name"] = name
        if self.default_profile == old:
            self.default_profile = name
        self.current_profile = name
        self._persist_profiles()
        self._refresh_profile_combo()
        self._update_profile_hint()
        print_log(f"✏️ 方案「{old}」已改名为「{name}」")

    def delete_profile(self):
        index = self._profile_index(self.current_profile)
        if index < 0:
            return
        if len(self.profiles) == 1:
            QMessageBox.warning(self, "错误", "至少保留一套方案")
            return

        answer = QMessageBox.question(
            self, "删除方案", f"确定删除方案「{self.current_profile}」？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        removed = self.profiles.pop(index)["name"]
        if self.default_profile == removed:
            self.default_profile = self.profiles[0]["name"]   # 默认不能悬空
        self.current_profile = self.profiles[min(index, len(self.profiles) - 1)]["name"]
        self._persist_profiles()
        self._refresh_profile_combo()
        self._apply_profile(self.profiles[self._profile_index(self.current_profile)])
        print_log(f"🗑 已删除方案「{removed}」")

    def set_default_profile(self):
        if self._profile_index(self.current_profile) < 0:
            return
        self.default_profile = self.current_profile
        self._persist_profiles()
        self._refresh_profile_combo()
        self._update_profile_hint()
        print_log(f"★ 方案「{self.default_profile}」已设为默认，下次启动自动载入")
