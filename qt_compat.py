"""Qt compatibility layer for PySide6, PySide2, and PyQt5."""

from __future__ import annotations

try:
    from PySide6.QtCore import Qt, QThread, QTimer, Slot, QRect, QSize, QPoint, QRectF, QObject, Signal
    from PySide6.QtGui import QPixmap, QColor, QBrush, QPainterPath, QMouseEvent
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QColorDialog,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFrame,
        QGroupBox,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QTabWidget,
        QVBoxLayout,
        QWidget,
        QFileDialog,
        QTextEdit,
        QGraphicsView,
        QGraphicsScene,
        QGraphicsPixmapItem,
        QGraphicsRectItem,
        QGraphicsItem,
        QSpinBox,
    )
    QtBindings = "PySide6"

except ImportError:
    try:
        from PySide2.QtCore import Qt, QThread, QTimer, Slot, QRect, QSize, QPoint, QRectF, QObject, Signal
        from PySide2.QtGui import QPixmap, QColor, QBrush, QPainterPath, QMouseEvent
        from PySide2.QtWidgets import (
            QApplication,
            QCheckBox,
            QColorDialog,
            QComboBox,
            QDialog,
            QDialogButtonBox,
            QFrame,
            QGroupBox,
            QGridLayout,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QMainWindow,
            QMessageBox,
            QPushButton,
            QTabWidget,
            QVBoxLayout,
            QWidget,
            QFileDialog,
            QTextEdit,
            QGraphicsView,
            QGraphicsScene,
            QGraphicsPixmapItem,
            QGraphicsRectItem,
            QGraphicsItem,
            QSpinBox,
        )
        QtBindings = "PySide2"

    except ImportError:
        try:
            from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSlot as Slot, QRect, QSize, QPoint, QRectF, QObject, pyqtSignal as Signal
            from PyQt5.QtGui import QPixmap, QColor, QBrush, QPainterPath, QMouseEvent
            from PyQt5.QtWidgets import (
                QApplication,
                QCheckBox,
                QColorDialog,
                QComboBox,
                QDialog,
                QDialogButtonBox,
                QFrame,
                QGroupBox,
                QGridLayout,
                QHBoxLayout,
                QLabel,
                QLineEdit,
                QMainWindow,
                QMessageBox,
                QPushButton,
                QTabWidget,
                QVBoxLayout,
                QWidget,
                QFileDialog,
                QTextEdit,
                QGraphicsView,
                QGraphicsScene,
                QGraphicsPixmapItem,
                QGraphicsRectItem,
                QGraphicsItem,
                QSpinBox,
            )
            QtBindings = "PyQt5"

        except ImportError as exc:
            raise ImportError(
                "Could not import Qt bindings. Install PySide6, PySide2, or PyQt5. "
                "On Raspberry Pi OS 32-bit, PySide6 wheels are not available via pip. "
                "Install a system package such as python3-pyqt5 or python3-pyside2."
            ) from exc

__all__ = [
    "Qt",
    "QThread",
    "QTimer",
    "Slot",
    "QRect",
    "QSize",
    "QPoint",
    "QRectF",
    "QPixmap",
    "QColor",
    "QBrush",
    "QPainterPath",
    "QApplication",
    "QCheckBox",
    "QColorDialog",
    "QComboBox",
    "QDialog",
    "QDialogButtonBox",
    "QFrame",
    "QGroupBox",
    "QGridLayout",
    "QHBoxLayout",
    "QLabel",
    "QLineEdit",
    "QMainWindow",
    "QMessageBox",
    "QPushButton",
    "QTabWidget",
    "QVBoxLayout",
    "QWidget",
    "QFileDialog",
    "QTextEdit",
    "QGraphicsView",
    "QGraphicsScene",
    "QGraphicsPixmapItem",
    "QGraphicsRectItem",
    "QGraphicsItem",
    "QSpinBox",
    "QObject",
    "Signal",
    "QMouseEvent",
    "QtBindings",
]
