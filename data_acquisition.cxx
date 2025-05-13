#include <iostream>
#include <vector>
#include <atomic>
#include <csignal>
#include <chrono>
#include <thread>
#include <pigpio.h>
#include <termios.h>
#include <unistd.h>
#include <fcntl.h>
#include <fstream>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <cstring>
#include <cerrno>

using namespace std;
using namespace chrono;

const int BIT_WIDTH = 13;
const int TRIGGER_PIN = 19;
const int X_PINS[BIT_WIDTH] = {18, 4, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 27};
const int Y_PINS[BIT_WIDTH] = {18, 23, 14, 15, 16, 17, 23, 20, 21, 21, 24, 25, 22};

atomic<bool> running(false);
atomic<int> pulseCount(0);

constexpr int DIM = 8192;
constexpr const char* SHM_NAME = "/zdata_shared";
uint64_t (*Z)[DIM] = nullptr;  // Pointer to shared memory matrix

int kbhit() {
    struct termios oldt, newt;
    int ch;
    int oldf;
    tcgetattr(STDIN_FILENO, &oldt);
    newt = oldt;
    newt.c_lflag &= ~(ICANON | ECHO);
    tcsetattr(STDIN_FILENO, TCSANOW, &newt);
    oldf = fcntl(STDIN_FILENO, F_GETFL, 0);
    fcntl(STDIN_FILENO, F_SETFL, oldf | O_NONBLOCK);
    ch = getchar();
    tcsetattr(STDIN_FILENO, TCSANOW, &oldt);
    fcntl(STDIN_FILENO, F_SETFL, oldf);
    if (ch != EOF) {
        ungetc(ch, stdin);
        return 1;
    }
    return 0;
}

char getKeyPress() {
    return getchar();
}

void handleSignal(int signum) {
    running = false;
}

int generateDecimalFromBits(uint32_t data, const int* pins) {
    int result = 0;
    for (int i = 0; i < BIT_WIDTH; ++i) {
        int bit = (data >> pins[i]) & 1;
        result |= (bit << (BIT_WIDTH - 1 - i));
    }
    return result;
}

void saveToCSV() {
    ofstream file("output_z_data.csv");
    file << "X,Y,Z\n";
    for (int x = 0; x < DIM; ++x) {
        for (int y = 0; y < DIM; ++y) {
            if (Z[x][y] != 0) {
                file << x << "," << y << "," << Z[x][y] << "\n";
            }
        }
    }
    file.close();
    cout << "\nSaved data to output_z_data.csv\n";
}

void triggerCallback(int gpio, int level, uint32_t tick) {
    if (level == 1) {
        uint32_t data = gpioRead_Bits_0_31();
        int x = generateDecimalFromBits(data, X_PINS);
        int y = generateDecimalFromBits(data, Y_PINS);
        if (x < DIM && y < DIM) {
            __sync_fetch_and_add(&Z[x][y], 1); // Thread-safe increment
            //cout<< "X=" << x << " Y=" << y;
            pulseCount++;
        }
    }
}

int main() {
    signal(SIGINT, handleSignal);

    // Setup shared memory
    int shm_fd = shm_open(SHM_NAME, O_CREAT | O_RDWR, 0666);
    if (shm_fd == -1) {
        cerr << "Failed to open shared memory: " << strerror(errno) << endl;
        return 1;
    }

    size_t shm_size = sizeof(uint64_t) * DIM * DIM;
    if (ftruncate(shm_fd, shm_size) == -1) {
        cerr << "Failed to set size: " << strerror(errno) << endl;
        return 1;
    }

    void* shm_ptr = mmap(nullptr, shm_size, PROT_READ | PROT_WRITE, MAP_SHARED, shm_fd, 0);
    if (shm_ptr == MAP_FAILED) {
        cerr << "Failed to map shared memory: " << strerror(errno) << endl;
        return 1;
    }

    Z = reinterpret_cast<uint64_t(*)[DIM]>(shm_ptr);
    memset(Z, 0, shm_size);  // Clear data

    // Setup GPIO
    if (gpioInitialise() < 0) {
        cerr << "Failed to initialize GPIO\n";
        return 1;
    }

    for (int i = 0; i < BIT_WIDTH; ++i) {
        gpioSetMode(X_PINS[i], PI_INPUT);
        gpioSetPullUpDown(X_PINS[i], PI_PUD_DOWN);
        gpioSetMode(Y_PINS[i], PI_INPUT);
        gpioSetPullUpDown(Y_PINS[i], PI_PUD_DOWN);
    }

    gpioSetMode(TRIGGER_PIN, PI_INPUT);
    gpioSetPullUpDown(TRIGGER_PIN, PI_PUD_DOWN);

    auto lastTime = steady_clock::now();
    int lastCount = 0;

    cout << "DAQ Control Program (Shared Memory)\n";
    cout << "Commands:\n";
    cout << "  T: Start data acquisition\n";
    cout << "  P: Pause data acquisition\n";
    cout << "  C: Clear data\n";
    cout << "  X: Exit program\n";

    while (true) {
        if (kbhit()) {
            char ch = getKeyPress();
            switch (ch) {
                case 'x': case 'X':
                    running = false;
                    goto exit_loop;
                case 't': case 'T':
                    gpioSetAlertFunc(TRIGGER_PIN, triggerCallback);
                    running = true;
                    break;
                case 'p': case 'P':
                    gpioSetAlertFunc(TRIGGER_PIN, nullptr);
                    running = false;
                    break;
                case 'c': case 'C':
                    memset(Z, 0, shm_size);
                    cout << "\nCleared all Z values.\n";
                    break;
            }
        }

        if (running) {
            auto now = steady_clock::now();
            if (duration_cast<seconds>(now - lastTime).count() >= 1) {
                int currentCount = pulseCount.load();
                int freq = currentCount - lastCount;
                lastCount = currentCount;
                lastTime = now;
                cout << "\rCurrent Frequency: " << freq << " Hz   " << flush;
            }
        }

        this_thread::sleep_for(milliseconds(1));
    }

exit_loop:
    gpioSetAlertFunc(TRIGGER_PIN, nullptr);
    gpioTerminate();

    cout << "\n\nStopped by user.\n";
    cout << "Do you want to save the data to CSV? (y/n): ";
    char opt;
    cin >> opt;
    if (opt == 'y' || opt == 'Y') {
        saveToCSV();
    }

    cout << "Do you want to see non-zero data? (y/n): ";
    cin >> opt;
    if (opt == 'y' || opt == 'Y') {
        for (int i = 0; i < DIM; ++i) {
            for (int j = 0; j < DIM; ++j) {
                if (Z[i][j] != 0) {
                    cout << i << "," << j << "," << Z[i][j] << "\n";
                }
            }
        }
    }

    // 💡 Unmap and clean up shared memory after all access
    munmap(Z, shm_size);
    close(shm_fd);
    shm_unlink(SHM_NAME);

    cout << "Program exited cleanly.\n";
    return 0;

}
