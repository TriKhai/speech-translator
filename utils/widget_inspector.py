from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import QObject, QEvent


class WidgetInspector(QObject):
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Enter and isinstance(obj, QWidget):
            print("=" * 40)
            print("Widget:", obj.__class__.__name__)
            print("Geometry:", obj.geometry())

            if obj.layout():
                m = obj.layout().contentsMargins()
                print(
                    "Margins:",
                    m.left(), m.top(), m.right(), m.bottom()
                )
                print("Spacing:", obj.layout().spacing())

            # FIX #5: Lưu styleSheet gốc trước khi highlight
            # để có thể restore lại khi Leave
            obj.setProperty("_orig_style", obj.styleSheet())

            # Highlight
            obj.setStyleSheet(
                obj.styleSheet()
                + "\nborder: 2px solid red;"
                + "background: rgba(255,0,0,0.04);"
            )

        elif event.type() == QEvent.Type.Leave and isinstance(obj, QWidget):
            # FIX #5: Restore styleSheet gốc khi chuột rời khỏi widget
            orig = obj.property("_orig_style")
            if orig is not None:
                obj.setStyleSheet(orig)

        return super().eventFilter(obj, event)


def install_inspector(app: QApplication):
    inspector = WidgetInspector()
    app.installEventFilter(inspector)
    return inspector