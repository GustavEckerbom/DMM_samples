"""
TC-08 Channel Calibration Tool

This script displays the TC-08 image and lets you click on each channel to record
exact pixel coordinates. Run this once to generate the correct channel_rects values.
"""

from qt_compat import QApplication, QLabel, QVBoxLayout, QWidget, QPixmap, QMouseEvent, Qt


class CalibrationWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TC-08 Channel Calibration")
        self.channel_positions = {}
        self.current_channel = 1
        
        layout = QVBoxLayout()
        
        self.image_label = QLabel()
        pixmap = QPixmap("TC-08img.png")
        self.image_label.setPixmap(pixmap)
        self.image_width = pixmap.width()
        self.image_height = pixmap.height()
        
        self.info_label = QLabel(
            f"Image size: {self.image_width}x{self.image_height}\n"
            "Click on channel 1 to start calibration.\n"
            "Click on the center of each number (1-8) in order."
        )
        
        layout.addWidget(self.info_label)
        layout.addWidget(self.image_label)
        self.setLayout(layout)
        
        self.image_label.mousePressEvent = self.on_image_clicked
    
    def on_image_clicked(self, event: QMouseEvent):
        if self.current_channel > 8:
            self.info_label.setText("Calibration complete! Check console output.")
            return
        
        pos = event.pos()
        self.channel_positions[self.current_channel] = (pos.x(), pos.y())
        
        self.info_label.setText(
            f"Channel {self.current_channel} at ({pos.x()}, {pos.y()})\n"
            f"Next: Click on channel {self.current_channel + 1}"
        )
        
        self.current_channel += 1
        
        if self.current_channel > 8:
            self.print_results()
    
    def print_results(self):
        print("\n" + "="*70)
        print("CALIBRATION COMPLETE!")
        print("="*70)
        print(f"\nImage dimensions: {self.image_width}x{self.image_height}")
        print("\nChannel center positions:")
        for ch in range(1, 9):
            if ch in self.channel_positions:
                x, y = self.channel_positions[ch]
                print(f"  Channel {ch}: ({x}, {y})")
        
        print("\n" + "="*70)
        print("Python code to paste into gui.py:")
        print("="*70)
        print("\nself.channel_rects = {")
        
        # Generate rects with ~45px width and ~30px height, centered on click points
        rect_width = 45
        rect_height = 30
        
        for ch in range(1, 9):
            if ch in self.channel_positions:
                cx, cy = self.channel_positions[ch]
                x = cx - rect_width // 2
                y = cy - rect_height // 2
                print(f"    {ch}: QRect({x}, {y}, {rect_width}, {rect_height}),")
        
        print("}\n")
        print("="*70)


if __name__ == "__main__":
    app = QApplication([])
    window = CalibrationWindow()
    window.show()
    app.exec()
