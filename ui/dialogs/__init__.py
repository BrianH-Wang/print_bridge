"""对话框：每个一个模块，互不依赖，方便单独改。"""

from ui.dialogs.advanced_search import AdvancedSearchDialog
from ui.dialogs.history_search import HistorySearchDialog
from ui.dialogs.http_response import HttpResponseDialog
from ui.dialogs.image_preview import ImagePreviewDialog
from ui.dialogs.printer import PrinterDialog

__all__ = [
    "AdvancedSearchDialog", "HistorySearchDialog", "HttpResponseDialog",
    "ImagePreviewDialog", "PrinterDialog",
]
