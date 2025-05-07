import os
import struct
import subprocess
import signal
import numpy as np
import time
from PyQt5 import QtWidgets, QtCore, QtGui
import pyqtgraph as pg
import pyqtgraph.opengl as gl
import sys

# Constants
PIPE_PATH = "/tmp/zdata_pipe"
CONTROL_PIPE_PATH = "/tmp/data_acquisition"
POINT_SIZE = 2.0
MAX_POINTS = 500000  # Adjust for performance

class RealTime3DPlotter(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('3D Real-Time Z(X,Y) Data Acquisition')
        self.resize(1200, 800)
        
        # Start the C++ program
        self.setup_pipes()
        self.start_cpp_process()
        
        # Main layout
        main_layout = QtWidgets.QHBoxLayout()
        self.setLayout(main_layout)
        
        # Left panel for controls
        left_panel = QtWidgets.QVBoxLayout()
        left_panel.setContentsMargins(10, 10, 10, 10)
        
        # Control buttons
        self.start_button = QtWidgets.QPushButton("Start Acquisition")
        self.pause_button = QtWidgets.QPushButton("Pause Acquisition")
        self.clear_button = QtWidgets.QPushButton("Clear Data")
        self.save_button = QtWidgets.QPushButton("Save to CSV")
        self.exit_button = QtWidgets.QPushButton("Exit")
        
        # Connect signals
        self.start_button.clicked.connect(self.start_acquisition)
        self.pause_button.clicked.connect(self.pause_acquisition)
        self.clear_button.clicked.connect(self.clear_data)
        self.save_button.clicked.connect(self.save_data)
        self.exit_button.clicked.connect(self.close_application)
        
        # Status indicators
        self.status_label = QtWidgets.QLabel("Status: Ready")
        self.frequency_label = QtWidgets.QLabel("Frequency: 0 Hz")
        self.points_label = QtWidgets.QLabel("Points: 0")
        
        # Add widgets to left panel
        left_panel.addWidget(QtWidgets.QLabel("<h2>DAQ Controls</h2>"))
        left_panel.addWidget(self.start_button)
        left_panel.addWidget(self.pause_button)
        left_panel.addWidget(self.clear_button)
        left_panel.addWidget(self.save_button)
        left_panel.addWidget(self.exit_button)
        left_panel.addStretch()
        left_panel.addWidget(QtWidgets.QLabel("<h3>Statistics</h3>"))
        left_panel.addWidget(self.status_label)
        left_panel.addWidget(self.frequency_label)
        left_panel.addWidget(self.points_label)
        left_panel.addStretch()
        
        # Create a widget for the left panel
        left_widget = QtWidgets.QWidget()
        left_widget.setLayout(left_panel)
        left_widget.setMaximumWidth(250)
        
        # Add left panel to main layout
        main_layout.addWidget(left_widget)
        
        # Right panel for 3D visualization
        right_panel = QtWidgets.QVBoxLayout()
        
        # GL View
        self.gl_view = gl.GLViewWidget()
        self.gl_view.opts['distance'] = 20000
        self.gl_view.setCameraPosition(elevation=40, azimuth=45)
        
        # Add widgets to right panel
        right_panel.addWidget(self.gl_view)
        
        # Create a widget for the right panel
        right_widget = QtWidgets.QWidget()
        right_widget.setLayout(right_panel)
        
        # Add right panel to main layout
        main_layout.addWidget(right_widget)
        
        # Add axes
        self.add_axes()
        
        # Data buffer
        self.data = np.zeros((0, 3), dtype=np.uint32)
        self.running = False
        self.last_count = 0
        self.last_time = time.time()
        
        # Scatter item
        self.scatter = gl.GLScatterPlotItem(pos=np.array([]), size=POINT_SIZE, color=(0, 0, 1, 1), pxMode=False)
        self.gl_view.addItem(self.scatter)
        
        # Open data pipe
        self.pipe = None
        self.open_data_pipe()
        
        # Timer for update loop
        self.timer = pg.QtCore.QTimer()
        self.timer.timeout.connect(self.update)
        self.timer.start(30)  # Update every 30ms
        
        # Set up a frequency update timer
        self.freq_timer = QtCore.QTimer()
        self.freq_timer.timeout.connect(self.update_frequency)
        self.freq_timer.start(1000)  # Update frequency every second
        
    def setup_pipes(self):
        # Create control pipe if it doesn't exist
        try:
            if not os.path.exists(CONTROL_PIPE_PATH):
                os.mkfifo(CONTROL_PIPE_PATH, 0o666)
            print(f"Control pipe created at {CONTROL_PIPE_PATH}")
        except OSError as e:
            if e.errno != errno.EEXIST:
                print(f"Failed to create control pipe: {e}")
                sys.exit(1)
    
    def start_cpp_process(self):
        # Path to the C++ executable - adjust as needed
        cpp_executable = "./daq_program"
        
        # Check if executable exists
        if not os.path.exists(cpp_executable):
            QtWidgets.QMessageBox.critical(
                self, 
                "Error", 
                f"C++ executable not found at {cpp_executable}. Please compile the C++ code first."
            )
            sys.exit(1)
        
        # Start the C++ process
        try:
            self.cpp_process = subprocess.Popen(
                [cpp_executable],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line buffered
                universal_newlines=True
            )
            print("C++ process started successfully")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to start C++ process: {e}")
            sys.exit(1)
    
    def open_data_pipe(self):
        # Try to open the data pipe
        max_attempts = 10
        for attempt in range(max_attempts):
            if os.path.exists(PIPE_PATH):
                try:
                    self.pipe = open(PIPE_PATH, "rb", buffering=0)
                    # Set non-blocking
                    fd = self.pipe.fileno()
                    flags = os.fcntl.fcntl(fd, os.fcntl.F_GETFL)
                    os.fcntl.fcntl(fd, os.fcntl.F_SETFL, flags | os.O_NONBLOCK)
                    print("Data pipe opened successfully")
                    return
                except Exception as e:
                    print(f"Attempt {attempt+1}/{max_attempts}: Failed to open data pipe: {e}")
            time.sleep(0.5)  # Wait before retrying
        
        # If we get here, we failed to open the pipe
        QtWidgets.QMessageBox.critical(
            self, 
            "Error", 
            "Failed to open data pipe. Make sure the C++ program is running."
        )
        self.close_application()
    
    def add_axes(self):
        # Add x, y, z axes
        x_axis = gl.GLAxisItem()
        y_axis = gl.GLAxisItem()
        z_axis = gl.GLAxisItem()
        
        x_axis.setSize(8192, 0, 0)
        y_axis.setSize(0, 8192, 0)
        z_axis.setSize(0, 0, 10000)
        
        # Add custom colors for axes
        x_axis.setColor(QtGui.QColor(255, 0, 0))  # Red for X
        y_axis.setColor(QtGui.QColor(0, 255, 0))  # Green for Y
        z_axis.setColor(QtGui.QColor(0, 0, 255))  # Blue for Z
        
        self.gl_view.addItem(x_axis)
        self.gl_view.addItem(y_axis)
        self.gl_view.addItem(z_axis)
        
        # Add grid
        grid = gl.GLGridItem()
        grid.setSize(8192, 8192, 0)
        grid.setSpacing(1024, 1024, 0)
        self.gl_view.addItem(grid)
    
    def update(self):
        # Read as many 12-byte chunks (x,y,z) as available
        if self.pipe:
            try:
                while True:
                    chunk = self.pipe.read(12)
                    if not chunk or len(chunk) < 12:
                        break
                    
                    x, y, z = struct.unpack('III', chunk)
                    self.data = np.vstack((self.data, np.array([[x, y, z]])))
                    
                    # Limit max number of points for performance
                    if len(self.data) > MAX_POINTS:
                        self.data = self.data[-MAX_POINTS:]
            except BlockingIOError:
                pass  # No data available
            except Exception as e:
                print(f"Error reading from pipe: {e}")
            
            # Update scatter plot if we have data
            if len(self.data) > 0:
                # Normalize Z values for coloring (logarithmic scale)
                norm_z = np.log1p(self.data[:, 2])
                max_z = np.max(norm_z) if len(norm_z) > 0 else 1
                
                # Create color array based on Z values
                colors = np.zeros((len(self.data), 4))
                colors[:, 0] = norm_z / max_z  # Red channel increases with Z
                colors[:, 2] = 1 - (norm_z / max_z)  # Blue channel decreases with Z
                colors[:, 3] = 0.5 + 0.5 * (norm_z / max_z)  # Alpha (transparency)
                
                # Update scatter plot
                self.scatter.setData(pos=self.data, size=POINT_SIZE, color=colors)
                
                # Update points label
                self.points_label.setText(f"Points: {len(self.data)}")
    
    def update_frequency(self):
        if self.running:
            current_count = len(self.data)
            current_time = time.time()
            
            # Calculate frequency (points per second)
            time_diff = current_time - self.last_time
            count_diff = current_count - self.last_count
            
            if time_diff > 0:
                frequency = count_diff / time_diff
                self.frequency_label.setText(f"Frequency: {int(frequency)} Hz")
            
            # Update last values
            self.last_count = current_count
            self.last_time = current_time
    
    def send_command(self, command):
        """Send a command to the C++ process via the control pipe"""
        try:
            with open(CONTROL_PIPE_PATH, "w") as control_pipe:
                control_pipe.write(command)
                control_pipe.flush()
                print(f"Sent command: {command}")
            return True
        except Exception as e:
            print(f"Failed to send command: {e}")
            return False
    
    def start_acquisition(self):
        success = self.send_command("T")
        if success:
            self.running = True
            self.status_label.setText("Status: Running")
            self.start_button.setEnabled(False)
            self.pause_button.setEnabled(True)
    
    def pause_acquisition(self):
        success = self.send_command("P")
        if success:
            self.running = False
            self.status_label.setText("Status: Paused")
            self.start_button.setEnabled(True)
            self.pause_button.setEnabled(False)
    
    def clear_data(self):
        success = self.send_command("C")
        if success:
            # Clear local data as well
            self.data = np.zeros((0, 3), dtype=np.uint32)
            self.scatter.setData(pos=np.array([]))
            self.points_label.setText("Points: 0")
            self.frequency_label.setText("Frequency: 0 Hz")
    
    def save_data(self):
        # First ask the C++ program to save the data
        success = self.send_command("S")
        if success:
            self.status_label.setText("Status: Data saved")
            QtWidgets.QMessageBox.information(
                self, 
                "Save Success", 
                "Data successfully saved to output_z_data.csv"
            )
    
    def close_application(self):
        # Stop the C++ process gracefully
        if hasattr(self, 'cpp_process'):
            self.send_command("X")
            # Give it a moment to shut down
            QtCore.QTimer.singleShot(500, self.force_close)
        else:
            self.force_close()
    
    def force_close(self):
        # Force close the C++ process if it's still running
        if hasattr(self, 'cpp_process') and self.cpp_process.poll() is None:
            self.cpp_process.terminate()
            try:
                self.cpp_process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.cpp_process.kill()
        
        # Close pipe if open
        if hasattr(self, 'pipe') and self.pipe:
            self.pipe.close()
        
        # Clean up pipes
        try:
            if os.path.exists(CONTROL_PIPE_PATH):
                os.unlink(CONTROL_PIPE_PATH)
        except:
            pass
        
        # Exit the application
        QtWidgets.QApplication.quit()
    
    def closeEvent(self, event):
        # Override close event to ensure clean shutdown
        self.close_application()
        event.accept()

if __name__ == '__main__':
    import os
    import fcntl
    import errno
    
    app = QtWidgets.QApplication([])
    win = RealTime3DPlotter()
    win.show()
    sys.exit(app.exec_())
