#include <iostream>
#include <vector>
#include <atomic>
#include <csignal>
#include <chrono>
#include <thread>
#include <pigpio.h>
#include <unistd.h>
#include <fcntl.h>
#include <fstream>
#include <sys/stat.h>
#include <sys/types.h>
#include <cerrno>
#include <cstring>

using namespace std;
using namespace chrono;

// Constants
const string DATA_PIPE_PATH = "/tmp/zdata_pipe";
const string CONTROL_PIPE_PATH = "/tmp/daq_control_pipe";
const int BIT_WIDTH = 13;
const int TRIGGER_PIN = 19;
const int X_PINS[BIT_WIDTH] = {18, 4, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 27};
const int Y_PINS[BIT_WIDTH] = {18, 23, 14, 15, 16, 17, 23, 20, 21, 21, 24, 25, 22};

// Global variables
atomic<bool> running(false);
atomic<bool> program_running(true);
vector<vector<uint64_t>> Z(8192, vector<uint64_t>(8192, 0));
atomic<int> pulseCount(0);
int data_pipe_fd = -1;
int control_pipe_fd = -1;

// Function prototypes
void handleSignal(int signum);
int generateDecimalFromBits(uint32_t data, const int* pins);
void saveToCSV(const vector<vector<uint64_t>>& Z);
void triggerCallback(int gpio, int level, uint32_t tick);
void setupPipes();
void commandListener();
void processPulses();
void cleanupAndExit();

int main() {
    // Set up signal handler for graceful termination
    signal(SIGINT, handleSignal);
    signal(SIGTERM, handleSignal);
    
    cout << "DAQ Program Starting..." << endl;
    
    // Initialize GPIO
    if (gpioInitialise() < 0) {
        cerr << "Failed to initialize GPIO" << endl;
        return 1;
    }
    
    // Configure GPIO pins
    for (int i = 0; i < BIT_WIDTH; ++i) {
        gpioSetMode(X_PINS[i], PI_INPUT);
        gpioSetPullUpDown(X_PINS[i], PI_PUD_DOWN);
        gpioSetMode(Y_PINS[i], PI_INPUT);
        gpioSetPullUpDown(Y_PINS[i], PI_PUD_DOWN);
    }
    
    gpioSetMode(TRIGGER_PIN, PI_INPUT);
    gpioSetPullUpDown(TRIGGER_PIN, PI_PUD_DOWN);
    
    // Set up named pipes
    setupPipes();
    
    cout << "DAQ Program Ready" << endl;
    cout << "Waiting for commands from Python interface..." << endl;
    
    // Start command listener thread
    thread commandThread(commandListener);
    
    // Main loop - just keep program alive while threads do the work
    while (program_running) {
        this_thread::sleep_for(milliseconds(100));
    }
    
    // Clean up
    if (commandThread.joinable()) {
        commandThread.join();
    }
    
    cleanupAndExit();
    
    cout << "DAQ Program exited cleanly." << endl;
    return 0;
}

void setupPipes() {
    // Create the data FIFO if it doesn't exist
    if (mkfifo(DATA_PIPE_PATH.c_str(), 0666) == -1 && errno != EEXIST) {
        perror("Failed to create data pipe");
        exit(1);
    }
    
    // Create the control FIFO if it doesn't exist
    if (mkfifo(CONTROL_PIPE_PATH.c_str(), 0666) == -1 && errno != EEXIST) {
        perror("Failed to create control pipe");
        unlink(DATA_PIPE_PATH.c_str());
        exit(1);
    }
    
    // Open the data pipe for writing
    cout << "Opening data pipe for writing..." << endl;
    data_pipe_fd = open(DATA_PIPE_PATH.c_str(), O_WRONLY);
    if (data_pipe_fd < 0) {
        perror("Failed to open data pipe");
        unlink(DATA_PIPE_PATH.c_str());
        unlink(CONTROL_PIPE_PATH.c_str());
        exit(1);
    }
    cout << "Data pipe opened successfully." << endl;
    
    // Open the control pipe for reading (non-blocking)
    cout << "Opening control pipe for reading..." << endl;
    control_pipe_fd = open(CONTROL_PIPE_PATH.c_str(), O_RDONLY | O_NONBLOCK);
    if (control_pipe_fd < 0) {
        perror("Failed to open control pipe");
        close(data_pipe_fd);
        unlink(DATA_PIPE_PATH.c_str());
        unlink(CONTROL_PIPE_PATH.c_str());
        exit(1);
    }
    cout << "Control pipe opened successfully." << endl;
}

void commandListener() {
    char command;
    while (program_running) {
        int bytes_read = read(control_pipe_fd, &command, 1);
        
        if (bytes_read == 1) {
            cout << "Received command: " << command << endl;
            
            switch (command) {
                case 'T':
                    cout << "Starting data acquisition..." << endl;
                    gpioSetAlertFunc(TRIGGER_PIN, triggerCallback);
                    running = true;
                    break;
                    
                case 'P':
                    cout << "Pausing data acquisition..." << endl;
                    gpioSetAlertFunc(TRIGGER_PIN, nullptr);
                    running = false;
                    break;
                    
                case 'C':
                    cout << "Clearing data..." << endl;
                    Z.assign(8192, vector<uint64_t>(8192, 0));
                    pulseCount = 0;
                    break;
                    
                case 'S':
                    cout << "Saving data to CSV..." << endl;
                    saveToCSV(Z);
                    break;
                    
                case 'X':
                    cout << "Exiting program..." << endl;
                    program_running = false;
                    break;
                    
                default:
                    cout << "Unknown command: " << command << endl;
                    break;
            }
        }
        
        // Don't hog CPU
        this_thread::sleep_for(milliseconds(50));
    }
}

void handleSignal(int signum) {
    cout << "Received signal " << signum << endl;
    program_running = false;
}

int generateDecimalFromBits(uint32_t data, const int* pins) {
    int result = 0;
    for (int i = 0; i < BIT_WIDTH; ++i) {
        int bit = (data >> pins[i]) & 1;
        result |= (bit << (BIT_WIDTH - 1 - i));
    }
    return result;
}

void saveToCSV(const vector<vector<uint64_t>>& Z) {
    ofstream file("output_z_data.csv");
    file << "X,Y,Z" << endl;
    
    // Count points for progress reporting
    uint64_t total_points = 0;
    uint64_t points_written = 0;
    
    // First count non-zero points
    for (int x = 0; x < 8192; ++x) {
        for (int y = 0; y < 8192; ++y) {
            if (Z[x][y] != 0) {
                total_points++;
            }
        }
    }
    
    cout << "Writing " << total_points << " points to CSV..." << endl;
    
    // Write the data
    for (int x = 0; x < 8192; ++x) {
        for (int y = 0; y < 8192; ++y) {
            if (Z[x][y] != 0) {
                file << x << "," << y << "," << Z[x][y] << endl;
                points_written++;
                
                // Report progress periodically
                if (points_written % 10000 == 0) {
                    cout << "Progress: " << (points_written * 100 / total_points) << "%" << endl;
                }
            }
        }
    }
    
    file.close();
    cout << "Data saved to output_z_data.csv" << endl;
}

void triggerCallback(int gpio, int level, uint32_t tick) {
    if (level == 1) {
        uint32_t data = gpioRead_Bits_0_31();
        int x = generateDecimalFromBits(data, X_PINS);
        int y = generateDecimalFromBits(data, Y_PINS);
        
        if (x < 8192 && y < 8192) {
            Z[x][y]++;
            pulseCount++;
            
            // Send data to Python via pipe
            uint32_t triplet[3] = {
                static_cast<uint32_t>(x),
                static_cast<uint32_t>(y),
                static_cast<uint32_t>(Z[x][y])
            };
            
            write(data_pipe_fd, triplet, sizeof(triplet));
        }
    }
}

void cleanupAndExit() {
    // Clean up GPIO
    gpioSetAlertFunc(TRIGGER_PIN, nullptr);
    gpioTerminate();
    
    // Close and remove pipes
    if (data_pipe_fd >= 0) {
        close(data_pipe_fd);
    }
    
    if (control_pipe_fd >= 0) {
        close(control_pipe_fd);
    }
}
