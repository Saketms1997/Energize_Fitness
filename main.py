from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from config.logging_config import configure_logging
from services import mongodb_service
from ui.main_window import APP_TITLE
from ui.startup import prepare
from ui.theme import stylesheet
from ui.widgets.wheel import WheelPassthrough


def main() -> int:
    configure_logging()
    log = logging.getLogger("gym")
    log.info("Starting %s", APP_TITLE)
    app = QApplication(sys.argv)
    app.aboutToQuit.connect(mongodb_service.close_client)
    app.setApplicationName(APP_TITLE)
    app.setStyleSheet(stylesheet())
    wheel = WheelPassthrough(app)  # keep mouse-wheel scrolling from sticking on inputs and tables
    app.installEventFilter(wheel)
    try:
        window = prepare()
    except Exception as exc:  # noqa: BLE001
        log.exception("Startup failed")
        QMessageBox.critical(None, APP_TITLE, f"The application could not start:\n\n{exc}\n\nSee the log file for details.")
        return 1
    if window is None:  # the owner cancelled setup or sign-in
        return 0
    window.show()
    window.show_page("Dashboard")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
