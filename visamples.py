from PySide6.QtWidgets import QApplication
import sys

from gui import DmmLoggerWindow


def main() -> None:
    app = QApplication(sys.argv)
    window = DmmLoggerWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
