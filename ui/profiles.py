"""配置方案：一个仓库一套配置，可命名、切换、设默认。

以 mixin 的形式挂在主窗口上——这些方法要读写各张卡片上的控件，
拆成独立组件反而要来回传一堆引用。
"""

import json
from pathlib import Path

from PyQt6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox

from channels import ANY_PATH, DEFAULT_CHANNEL_KEY, make_channel
from config_io import (
    build_export, bundled_configs_dir, dumps, oneway_profile, parse_import, sorter_profile,
)
from logger import print_log
from printer_utils import (
    DEFAULT_PRINTER_KEY, SYSTEM_PRINTER, get_available_printers, make_printer,
)
from ui.constants import (
    DATA_ADDR, DEFAULT_PROFILE_NAME, LEGACY_PROFILE_NAMES, MAX_PROFILES, ONEWAY_PROFILE_NAME,
)
from ui.dialogs.template import SAVE as TEMPLATE_SAVE
from ui.dialogs.template import TemplateDialog
from ui.helpers import normalize_addr, sanitize_channels, sanitize_printers


# 老版本首次运行建的「单向获取数据」原封不动时的通道，读方案时认出来就升级
_OLD_ONEWAY_CHANNELS = [make_channel(key=DEFAULT_CHANNEL_KEY, label="默认", path=ANY_PATH)]

# 几何智造模板建的两条通道：老版本把设备的 code 转成 expressNo，现在前端要 shipmentCode
_SORTER_CHANNEL_KEYS = ("sorter_data", "sorter_image")


def _upgrade_sorter_code_field(channels):
    """把模板建出来的 code → expressNo 改成 code → shipmentCode，配对字段跟着改。

    只认模板原样的那一条映射（source=code、target=expressNo），用户自己改过的不碰。
    返回是否改动过。
    """
    changed = False
    for channel in channels:
        if channel.get("key") not in _SORTER_CHANNEL_KEYS:
            continue
        fields = (channel.get("mapping") or {}).get("fields") or []
        renamed = False
        for field in fields:
            if field.get("source") == "code" and field.get("target") == "expressNo":
                field["target"] = "shipmentCode"
                renamed = True
        pair = channel.get("pair")
        if renamed and pair and pair.get("field") == "expressNo":
            pair["field"] = "shipmentCode"
        changed = changed or renamed
    return changed


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

        names, upgraded = set(), False
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            # 老名字（默认 / 分拣设备）改成新名字；新名字已经被占了就保留老的，不覆盖
            wanted = LEGACY_PROFILE_NAMES.get(name)
            if wanted and wanted not in names and not any(
                    str(o.get("name", "")).strip() == wanted for o in items if isinstance(o, dict)):
                name = wanted
            if not name or name in names:
                continue
            names.add(name)
            profile = self._sanitize_profile(name, item)
            if name == ONEWAY_PROFILE_NAME and profile["channels"] == _OLD_ONEWAY_CHANNELS:
                # 老版本建的单向方案只有一条没样例的兜底通道，用户也没动过：
                # 换成现在的 /callback 单向通道，模拟设备才有对的报文可发
                profile["channels"] = sanitize_channels(oneway_profile())
                print_log(f"🔧 方案「{name}」的接收通道已更新为 /callback 单向")
                upgraded = True
            if _upgrade_sorter_code_field(profile["channels"]):
                print_log(f"🔧 方案「{name}」的单号字段已改为 code → shipmentCode")
                upgraded = True
            profiles.append(profile)

        if not profiles:
            # 首次运行：排第一的就是 ★ 默认方案——双向获取数据（几何智造分拣设备模板）
            profiles.append(self._sanitize_profile(DEFAULT_PROFILE_NAME, sorter_profile()))
            # 单向方案一并建好：把上一版本记住的地址带过来，不让用户重填
            addr = normalize_addr(self.settings.value("data_addr")) or DATA_ADDR
            # 首次运行顺手把系统默认队列配成一台打印机，省得开箱是空表
            queues, default_queue = get_available_printers()
            seed = [make_printer(DEFAULT_PRINTER_KEY, "默认", SYSTEM_PRINTER,
                                 queue=default_queue or (queues[0] if queues else ""))]
            oneway = oneway_profile()
            oneway["data_addr"] = addr
            oneway["printers"] = seed if (queues or default_queue) else []
            profiles.append(self._sanitize_profile(ONEWAY_PROFILE_NAME, oneway))
        if upgraded:   # 存回去，下次启动不用再认一遍
            self.settings.setValue("profiles", json.dumps(profiles[:MAX_PROFILES], ensure_ascii=False))
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
            # 老方案里的 flow_mode / reply_timeout / dispatch_mode 在这里被
            # sanitize_channels 收成一条兜底通道，之后就不再单独存了
            "channels": sanitize_channels(item),
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
            "channels": [dict(c) for c in self.channel_list.channels],
        }

    def _apply_profile(self, profile):
        """把方案写回各个输入框；期间屏蔽 dirty 检测，避免自己把自己标成已修改。"""
        self._applying_profile = True
        try:
            self.port_input.setText(profile["ws_port"])
            self.addr_input.setText(profile["data_addr"])
            self.print_checkbox.setChecked(profile["enable_print"])
            self.data_checkbox.setChecked(profile["enable_data"])

            self.channel_list.set_channels(profile["channels"])
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

    # ------------------------------------------------------------------ #
    # 整套配置进出磁盘
    # ------------------------------------------------------------------ #

    def export_profile(self):
        """把当前方案存成 json，可以发给别的门店直接导入。"""
        index = self._profile_index(self.current_profile)
        if index < 0:
            return

        # 导出界面上此刻的样子，不是上次保存的样子：有未保存改动时
        # 导出个旧版本最坑人，而且他还不知道
        profile = {"name": self.current_profile, **self._current_config()}
        payload = build_export(profile, self.http_responses)

        path, _ = QFileDialog.getSaveFileName(
            self, "导出配置", f"{self.current_profile}.json", "配置文件 (*.json)"
        )
        if not path:
            return
        try:
            Path(path).write_text(dumps(payload), encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, "错误", f"写入失败：{e}")
            return
        print_log(f"📤 已导出方案「{self.current_profile}」到 {path}")
        QMessageBox.information(self, "导出配置", f"已导出到\n{path}")

    def import_profile(self):
        # 随程序发出去的现成配置在 configs/ 里，对话框直接打开到那，少翻一层
        start_dir = bundled_configs_dir()
        path, _ = QFileDialog.getOpenFileName(
            self, "导入配置", str(start_dir) if start_dir else "",
            "配置文件 (*.json);;所有文件 (*)",
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, "错误", f"读取失败：{e}")
            return

        profile, responses, problems = parse_import(text)
        if problems:
            QMessageBox.warning(self, "导入配置", "这个文件用不了：\n\n" + "\n".join(problems))
            return
        self._install_profile(profile, responses, f"文件 {Path(path).name}")

    def open_templates(self):
        dialog = TemplateDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        payload = dialog.payload()
        if dialog.action == TEMPLATE_SAVE:
            path, _ = QFileDialog.getSaveFileName(
                self, "保存模板", f"{dialog.name()}.json", "配置文件 (*.json)"
            )
            if not path:
                return
            try:
                Path(path).write_text(dumps(payload), encoding="utf-8")
            except OSError as e:
                QMessageBox.warning(self, "错误", f"写入失败：{e}")
                return
            print_log(f"📄 已保存模板「{dialog.name()}」到 {path}")
            QMessageBox.information(
                self, "配置模板",
                f"已保存到\n{path}\n\n改完之后用「导入」装回来。"
            )
            return

        self._install_profile(payload["profile"], payload.get("http_responses"),
                              f"模板「{dialog.name()}」")

    def _install_profile(self, incoming, responses, source):
        """把导入/模板来的方案装进来。重名时问一句，绝不静默覆盖。"""
        if len(self.profiles) >= MAX_PROFILES:
            QMessageBox.warning(
                self, "错误", f"最多保存 {MAX_PROFILES} 套方案，请先删除不用的"
            )
            return

        name = str(incoming.get("name", "")).strip()
        index = self._profile_index(name)
        if index >= 0:
            answer = QMessageBox.question(
                self, "导入配置",
                f"已经有一套叫「{name}」的方案了。\n\n"
                "「Yes」覆盖它，「No」另存为一套新的，「Cancel」放弃。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return
            if answer == QMessageBox.StandardButton.No:
                name = self._unused_name(name)
                index = -1

        profile = self._sanitize_profile(name, incoming)
        if index >= 0:
            self.profiles[index] = profile
        else:
            self.profiles.append(profile)

        # 应答表是全局的，跟着一起进来——只装通道不装应答等于只装了一半
        if responses:
            merged = dict(self.http_responses)
            for key, entry in responses.items():
                if key in merged and isinstance(entry, dict):
                    merged[key] = {**merged[key], **entry}
            self.http_responses = merged
            self.settings.setValue(
                "http_responses", json.dumps(self.http_responses, ensure_ascii=False)
            )

        self.current_profile = name
        self._persist_profiles()
        self._refresh_profile_combo()
        self._apply_profile(self.profiles[self._profile_index(name)])
        print_log(f"📥 已从{source}导入方案「{name}」")

    def _unused_name(self, base):
        """给重名的方案找个空位：仓库A → 仓库A (2) → 仓库A (3)…"""
        for n in range(2, MAX_PROFILES + 2):
            candidate = f"{base} ({n})"
            if self._profile_index(candidate) < 0:
                return candidate
        return f"{base} ({MAX_PROFILES})"

    def set_default_profile(self):
        if self._profile_index(self.current_profile) < 0:
            return
        self.default_profile = self.current_profile
        self._persist_profiles()
        self._refresh_profile_combo()
        self._update_profile_hint()
        print_log(f"★ 方案「{self.default_profile}」已设为默认，下次启动自动载入")
