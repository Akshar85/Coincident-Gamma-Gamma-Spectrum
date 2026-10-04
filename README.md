# Coincident Gamma-Gamma Spectrum

A prototype for recording coincident detector events on a Linux system and viewing the resulting spectrum in a PyQtGraph interface.

The project contains a C++ GPIO acquisition program and a Python visualizer. The acquisition program records counts in shared memory; the visualizer reads that memory and displays changed bins as a 3D scatter plot or a 2D heatmap.

## Repository contents

- `data_acquisition.cxx` — reads GPIO state on trigger events, updates the coincidence matrix, and optionally saves nonzero counts to CSV.
- `Real_Time Plot via PyQtGraph.py` — reads the shared-memory matrix and displays the data.
- `SignedReport.pdf` — project report and background.

## Requirements

The acquisition program is written for Linux and uses Raspberry Pi GPIO through `pigpio`.

- A Linux system with GPIO hardware supported by `pigpio`
- `pigpio` development headers and library
- A C++ compiler
- Python 3
- NumPy, PyQt5, and PyQtGraph

Install the Python packages with:

```bash
python3 -m pip install numpy PyQt5 pyqtgraph
```

The visualizer also uses PyQtGraph’s OpenGL module for the 3D view. A working OpenGL-capable graphics environment may be needed.

## Build and run

Compile the acquisition program on the target Linux system:

```bash
g++ -std=c++17 -pthread -o data_acquisition data_acquisition.cxx -lpigpio -lrt
```

Start acquisition:

```bash
sudo ./data_acquisition
```

The program creates the shared-memory object `/zdata_shared`, clears it at startup, and waits for a console command:

- `T` — begin listening for trigger events
- `P` — stop listening for trigger events
- `C` — clear the coincidence matrix
- `X` — exit, then choose whether to save a CSV and print nonzero bins

In a second terminal, run the visualizer:

```bash
python3 "Real_Time Plot via PyQtGraph.py"
```

The visualizer starts its shared-memory reader automatically. Its Start and Pause buttons start and stop that reader; they do not start or pause the C++ acquisition program. Use the C++ console commands to control event acquisition.

The visualizer expects the shared-memory object to exist when it starts. Start the C++ program first. On exit, the C++ program unlinks the shared-memory object, so close or restart the visualizer when the acquisition program exits.

## Data and memory

The program uses an `8192 × 8192` matrix of 64-bit unsigned counts. The matrix occupies approximately **512 MiB** of shared memory.

Through this approach, the Raspberry Pi can detect pin changes at approximately `80 kHz`, allowing the program to handle input data at that frequency.

Each recorded event increments one matrix cell. The optional CSV output, `output_z_data.csv`, contains nonzero cells in this format:

```text
X,Y,Z
```

- `X` — first detector’s decoded bin
- `Y` — second detector’s decoded bin
- `Z` — event count for that pair

The matrix indices are decoded 13-bit values. The code does not apply an energy calibration, so the visualizer’s axis labels should not be interpreted as calibrated keV values without additional calibration.

## Hardware configuration

GPIO assignments are defined near the top of `data_acquisition.cxx`:

- Trigger input: GPIO 19
- Detector bit mappings: `X_PINS` and `Y_PINS`

Review these assignments against the detector wiring before use. The current arrays contain repeated GPIO numbers, so verify that this matches the intended hardware mapping. The program reads the GPIO bank as a bit field and uses the configured pin numbers as bit positions.

## Visualization notes

The visualizer offers:

- A 3D scatter view
- A 2D heatmap with selectable display-grid resolution

The heatmap resolution changes the display grid; it does not change the acquisition matrix. The reader compares the shared matrix with a previous copy and scans the full matrix repeatedly. This can use substantial memory bandwidth and processing time, especially on a Raspberry Pi.

The visualizer looks for the shared-memory file at `/dev/shm/zdata_shared`, as expected on typical Linux systems using POSIX shared memory.

## Limitations

This is a research prototype. Validate the wiring, GPIO mapping, trigger behavior, and any required energy calibration against the actual detector setup before relying on the output. The repository does not include a Python dependency lockfile or an automated setup script.

The acquisition system is designed to match an 80 kHz frequency limit. Confirm this limit against the detector and hardware setup before operation.