#!/usr/bin/env python3
import sys
import os
import mmap
import numpy as np
import time
import signal

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QThread
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QPushButton, QHBoxLayout, QLabel, QGridLayout, 
                             QCheckBox, QSlider, QComboBox, QGroupBox,
                             QMessageBox)
import pyqtgraph as pg
import pyqtgraph.opengl as gl

# Constants
DIM = 8192
SHM_NAME = '/zdata_shared'
shm_size = DIM * DIM * 8  # uint64_t = 8 bytes
UPDATE_INTERVAL_MS = 100  # Update plot every 100 ms
MAX_DISPLAY_POINTS = 100000  # Maximum points to display at once

class DataReaderThread(QThread):
    """Thread to continuously read data from shared memory"""
    new_data_signal = pyqtSignal(object, object, object)
    stats_update_signal = pyqtSignal(int, float)
    error_signal = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self.running = False
        self.shm = None
        self.fd = None
        self.points_read = 0
        self.start_time = time.time()
    
    def open_shared_memory(self):
        """Open the shared memory for reading"""
        try:
            if not os.path.exists(f"/dev/shm{SHM_NAME}"):
                self.error_signal.emit(f"Shared memory does not exist at /dev/shm{SHM_NAME}")
                return False
            
            print(f"Opening shared memory for reading: {SHM_NAME}")
            self.fd = os.open(f"/dev/shm{SHM_NAME}", os.O_RDONLY)
            self.shm = mmap.mmap(self.fd, shm_size, mmap.MAP_SHARED, mmap.PROT_READ)
            return True
        except OSError as e:
            error_msg = f"Error opening shared memory: {e}"
            print(error_msg)
            self.error_signal.emit(error_msg)
            return False
    
    def run(self):
        """Main thread loop to read data from shared memory"""
        if not self.open_shared_memory():
            return
            
        self.running = True
        self.points_read = 0
        self.start_time = time.time()
        
        last_z_matrix = np.zeros((DIM, DIM), dtype=np.uint64)
        
        while self.running:
            try:
                # Read the entire matrix from shared memory
                Z = np.frombuffer(self.shm, dtype=np.uint64).reshape(DIM, DIM)
                
                # Find differences from last read
                diff = Z != last_z_matrix
                if np.any(diff):
                    x, y = np.where(diff)
                    z = Z[x, y]
                    
                    self.points_read += len(x)
                    # Emit new data points
                    self.new_data_signal.emit(x, y, z)
                    
                    # Update stats every 100 points
                    if self.points_read % 100 == 0:
                        elapsed = time.time() - self.start_time
                        rate = self.points_read / elapsed if elapsed > 0 else 0
                        self.stats_update_signal.emit(self.points_read, rate)
                    
                    # Update reference matrix
                    last_z_matrix = Z.copy()
                
                # Sleep briefly to reduce CPU usage
                time.sleep(0.01)
                
            except Exception as e:
                error_msg = f"Error reading from shared memory: {e}"
                print(error_msg)
                self.error_signal.emit(error_msg)
                time.sleep(0.1)
    
    def stop(self):
        """Stop the thread"""
        self.running = False
        if hasattr(self, 'shm') and self.shm is not None:
            try:
                self.shm.close()
            except:
                pass
        if hasattr(self, 'fd') and self.fd is not None:
            try:
                os.close(self.fd)
            except:
                pass
        self.wait()


class MainWindow(QMainWindow):
    """Main application window"""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Coincident Gamma-Gamma Spectrum Visualizer")
        self.resize(1200, 800)
        
        # Initialize data structures
        self.data_x = np.array([], dtype=np.uint32)
        self.data_y = np.array([], dtype=np.uint32)
        self.data_z = np.array([], dtype=np.uint32)
        self.view_mode = '3D Scatter'
        
        # Initialize the downsampling bins for heatmap
        self.grid_bins = 256  # Default downsampled grid size
        self.grid_data = np.zeros((self.grid_bins, self.grid_bins), dtype=np.float32)
        
        # Set up data reader thread
        self.data_reader = DataReaderThread()
        self.data_reader.new_data_signal.connect(self.on_new_data)
        self.data_reader.stats_update_signal.connect(self.update_stats)
        self.data_reader.error_signal.connect(self.show_error)
        
        # Set up the UI
        self.setup_ui()
        
        # Add status message
        self.statusBar().showMessage("Ready. Click 'Start Acquisition' to begin.")
        
        # Set up signal handler for clean exit
        signal.signal(signal.SIGINT, self.signal_handler)
        
        # Start with acquisition
        self.start_acquisition()
        
    def signal_handler(self, sig, frame):
        """Handle SIGINT (Ctrl+C)"""
        print("Received SIGINT. Cleaning up...")
        self.cleanup()
        sys.exit(0)
    
    def show_error(self, message):
        """Display an error message box"""
        self.statusBar().showMessage(f"Error: {message}")
        QMessageBox.critical(self, "Error", message)
    
    def setup_ui(self):
        """Set up the user interface"""
        # Create central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        layout = QVBoxLayout(central_widget)
        
        # Top controls
        top_controls = QHBoxLayout()
        
        # Create control buttons
        self.start_button = QPushButton("Start Acquisition")
        self.start_button.clicked.connect(self.start_acquisition)
        top_controls.addWidget(self.start_button)
        
        self.pause_button = QPushButton("Pause Acquisition")
        self.pause_button.clicked.connect(self.pause_acquisition)
        self.pause_button.setEnabled(False)
        top_controls.addWidget(self.pause_button)
        
        self.clear_button = QPushButton("Clear Data")
        self.clear_button.clicked.connect(self.clear_data)
        top_controls.addWidget(self.clear_button)
        
        # View mode selector
        top_controls.addWidget(QLabel("View Mode:"))
        self.view_mode_combo = QComboBox()
        self.view_mode_combo.addItems(['3D Scatter', '2D Heatmap'])
        self.view_mode_combo.currentTextChanged.connect(self.change_view_mode)
        top_controls.addWidget(self.view_mode_combo)
        
        # Grid size control for heatmap
        top_controls.addWidget(QLabel("Grid Size:"))
        self.grid_size_combo = QComboBox()
        self.grid_size_combo.addItems(['64', '128', '256', '512', '1024'])
        self.grid_size_combo.setCurrentText(str(self.grid_bins))
        self.grid_size_combo.currentTextChanged.connect(self.change_grid_size)
        top_controls.addWidget(self.grid_size_combo)
        
        # Add top controls to main layout
        layout.addLayout(top_controls)
        
        # Create plot widget container
        self.plot_container = QWidget()
        self.plot_layout = QVBoxLayout(self.plot_container)
        layout.addWidget(self.plot_container)
        
        # Create 3D plot widget
        self.setup_3d_view()
        
        # Create 2D heatmap widget (hidden initially)
        self.setup_2d_view()
        self.heatmap_widget.hide()
        
        # Statistics display
        stats_box = QGroupBox("Statistics")
        stats_layout = QGridLayout()
        stats_box.setLayout(stats_layout)
        
        self.points_label = QLabel("Points: 0")
        stats_layout.addWidget(self.points_label, 0, 0)
        
        self.rate_label = QLabel("Rate: 0 points/sec")
        stats_layout.addWidget(self.rate_label, 0, 1)
        
        self.shm_status_label = QLabel(f"Shared Memory: Not connected")
        stats_layout.addWidget(self.shm_status_label, 1, 0, 1, 2)
        
        layout.addWidget(stats_box)
        
        # Set up a timer for periodic UI updates
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_display)
        self.update_timer.start(50)  # Update every 50ms
    
    def setup_3d_view(self):
        """Set up the 3D visualization"""
        self.gl_widget = gl.GLViewWidget()
        
        # Set up axes
        axis = gl.GLAxisItem()
        axis.setSize(x=DIM, y=DIM, z=1000)
        self.gl_widget.addItem(axis)
        
        # Set up the 3D scatter plot with improved settings
        self.scatter = gl.GLScatterPlotItem(
            pos=np.zeros((1, 3)), 
            color=(0.2, 0.8, 0.5, 1), 
            size=5,  # Increased point size for better visibility
            pxMode=True  # Use pixel mode for consistent point size
        )
        self.gl_widget.addItem(self.scatter)
        
        # Set camera position for better initial view
        self.gl_widget.setCameraPosition(distance=DIM*1.5, elevation=30, azimuth=45)
        
        # Add the GL widget to the plot layout
        self.plot_layout.addWidget(self.gl_widget)
    
    def setup_2d_view(self):
        """Set up the 2D heatmap visualization"""
        self.heatmap_widget = pg.PlotWidget()
        self.heatmap_widget.setAspectLocked(True)
        self.heatmap_widget.setTitle("Coincident Gamma-Gamma Heatmap")
        self.heatmap_widget.setLabel('bottom', 'Energy 1 (keV)')
        self.heatmap_widget.setLabel('left', 'Energy 2 (keV)')
        
        # Create the heatmap item
        self.heatmap = pg.ImageItem()
        self.heatmap_widget.addItem(self.heatmap)
        
        # Add colorbar
        colormap = pg.colormap.get('viridis')
        self.heatmap.setColorMap(colormap)
        
        # Add colorbar
        self.colorbar = pg.ColorBarItem(values=(0, 1), colorMap=colormap)
        self.colorbar.setImageItem(self.heatmap)
        
        # Add the heatmap widget to the plot layout
        self.plot_layout.addWidget(self.heatmap_widget)
    
    def change_grid_size(self, size_str):
        """Change the grid size for the heatmap"""
        self.grid_bins = int(size_str)
        # Reset the grid data with new size
        self.grid_data = np.zeros((self.grid_bins, self.grid_bins), dtype=np.float32)
        # Repopulate from existing data
        if len(self.data_x) > 0:
            for x, y, z in zip(self.data_x, self.data_y, self.data_z):
                bin_x = int(x * self.grid_bins / DIM)
                bin_y = int(y * self.grid_bins / DIM)
                if 0 <= bin_x < self.grid_bins and 0 <= bin_y < self.grid_bins:
                    self.grid_data[bin_y, bin_x] = z
    
    def start_acquisition(self):
        """Start data acquisition"""
        self.start_button.setEnabled(False)
        self.pause_button.setEnabled(True)
        # Start the data reader thread if it's not already running
        if not self.data_reader.isRunning():
            self.data_reader.start()
            self.statusBar().showMessage("Acquisition started")
    
    def pause_acquisition(self):
        """Pause data acquisition"""
        self.start_button.setEnabled(True)
        self.pause_button.setEnabled(False)
        if self.data_reader.isRunning():
            self.data_reader.stop()
            self.statusBar().showMessage("Acquisition paused")
    
    def clear_data(self):
        """Clear accumulated data"""
        # Clear local data structures
        self.data_x = np.array([], dtype=np.uint32)
        self.data_y = np.array([], dtype=np.uint32)
        self.data_z = np.array([], dtype=np.uint32)
        self.grid_data = np.zeros((self.grid_bins, self.grid_bins), dtype=np.float32)
        self.update_display()
        self.statusBar().showMessage("Data cleared")
    
    def change_view_mode(self, mode):
        """Change the visualization mode"""
        self.view_mode = mode
        if mode == '3D Scatter':
            self.gl_widget.show()
            self.heatmap_widget.hide()
        else:  # 2D Heatmap
            self.gl_widget.hide()
            self.heatmap_widget.show()
    
    def on_new_data(self, x_arr, y_arr, z_arr):
        """Handle new data points from the reader thread"""
        # Add to our arrays, keeping only the most recent MAX_DISPLAY_POINTS
        if len(x_arr) > 0:
            self.data_x = np.append(self.data_x, x_arr)
            self.data_y = np.append(self.data_y, y_arr)
            self.data_z = np.append(self.data_z, z_arr)
            
            if len(self.data_x) > MAX_DISPLAY_POINTS:
                self.data_x = self.data_x[-MAX_DISPLAY_POINTS:]
                self.data_y = self.data_y[-MAX_DISPLAY_POINTS:]
                self.data_z = self.data_z[-MAX_DISPLAY_POINTS:]
            
            # Update the grid data for the heatmap
            for x, y, z in zip(x_arr, y_arr, z_arr):
                # Map x,y to grid bins
                bin_x = int(x * self.grid_bins / DIM)
                bin_y = int(y * self.grid_bins / DIM)
                
                if 0 <= bin_x < self.grid_bins and 0 <= bin_y < self.grid_bins:
                    self.grid_data[bin_y, bin_x] = z  # Note: y is first index for image display
    
    def update_stats(self, points, rate):
        """Update statistics display"""
        self.points_label.setText(f"Points: {points}")
        self.rate_label.setText(f"Rate: {rate:.1f} points/sec")
    
    def update_display(self):
        """Update the visualization with current data"""
        # Update shared memory status
        self.update_shm_status()
        
        if len(self.data_x) > 0:
            if self.view_mode == '3D Scatter':
                # Create positions array for 3D scatter
                pos = np.column_stack((self.data_x, self.data_y, self.data_z))
                
                # Normalize z values for color mapping
                z_max = max(1, np.max(self.data_z))
                z_norm = self.data_z / z_max
                
                # Improved color mapping with better visibility
                colors = np.zeros((len(z_norm), 4))
                colors[:, 0] = z_norm  # Red channel
                colors[:, 1] = 0.5 * (1-z_norm)  # Green channel
                colors[:, 2] = 1-z_norm  # Blue channel
                colors[:, 3] = 1.0  # Alpha channel (full opacity)
                
                # Update the scatter plot
                self.scatter.setData(pos=pos, color=colors, size=5)
            else:  # 2D Heatmap
                # Log scale for better visualization
                display_data = np.log1p(self.grid_data)  # log(1+x) to handle zeros
                max_val = np.max(display_data)
                if max_val > 0:
                    # Normalize
                    display_data = display_data / max_val
                
                # Update range for colorbar
                self.colorbar.setLevels((0, max_val))
                
                # Update the image
                self.heatmap.setImage(display_data)
    
    def update_shm_status(self):
        """Update shared memory status label"""
        if os.path.exists(f"/dev/shm{SHM_NAME}"):
            if self.data_reader.isRunning():
                self.shm_status_label.setText(f"Shared Memory: Connected and reading")
                self.shm_status_label.setStyleSheet("color: green")
            else:
                self.shm_status_label.setText(f"Shared Memory: Available but not reading")
                self.shm_status_label.setStyleSheet("color: orange")
        else:
            self.shm_status_label.setText(f"Shared Memory: Not found at /dev/shm{SHM_NAME}")
            self.shm_status_label.setStyleSheet("color: red")
    
    def cleanup(self):
        """Clean up resources before closing"""
        # Stop the thread
        if self.data_reader.isRunning():
            self.data_reader.stop()
        
        # Stop timers
        if hasattr(self, 'update_timer'):
            self.update_timer.stop()
    
    def closeEvent(self, event):
        """Handle window close event"""
        self.cleanup()
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
