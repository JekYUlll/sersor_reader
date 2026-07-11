#include "serial_io.h"

#include "util.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

static speed_t baud_to_speed(int baud) {
    switch (baud) {
        case 1200:
            return B1200;
        case 2400:
            return B2400;
        case 4800:
            return B4800;
        case 9600:
            return B9600;
        case 19200:
            return B19200;
        case 38400:
            return B38400;
        case 57600:
            return B57600;
        case 115200:
            return B115200;
        default:
            return (speed_t)0;
    }
}

void serial_port_reset(serial_port_t *port) {
    memset(port, 0, sizeof(*port));
    port->fd = -1;
    port->gpio_value_fd = -1;
    port->direction_gpio = -1;
}

static int write_text_file(const char *path, const char *text) {
    int fd = open(path, O_WRONLY | O_CLOEXEC);
    int saved_errno;

    if (fd < 0) {
        return -1;
    }
    if (write_full(fd, text, strlen(text)) != 0) {
        saved_errno = errno;
        (void)close(fd);
        errno = saved_errno;
        return -1;
    }
    return close(fd);
}

static int gpio_prepare(int gpio) {
    char value_path[128];
    char direction_path[128];
    char number[32];
    unsigned int attempt;
    int fd;

    (void)snprintf(value_path, sizeof(value_path), "/sys/class/gpio/gpio%d/value", gpio);
    (void)snprintf(direction_path, sizeof(direction_path), "/sys/class/gpio/gpio%d/direction", gpio);
    if (access(value_path, F_OK) != 0) {
        (void)snprintf(number, sizeof(number), "%d", gpio);
        if (write_text_file("/sys/class/gpio/export", number) != 0 && errno != EBUSY) {
            return -1;
        }
    }
    for (attempt = 0; attempt < 50U && access(value_path, F_OK) != 0; ++attempt) {
        struct timespec delay = {.tv_sec = 0, .tv_nsec = 20000000L};
        (void)nanosleep(&delay, NULL);
    }
    if (write_text_file(direction_path, "out") != 0) {
        return -1;
    }
    fd = open(value_path, O_WRONLY | O_CLOEXEC);
    if (fd < 0) {
        return -1;
    }
    if (write_full(fd, "0", 1U) != 0) {
        int saved_errno = errno;
        (void)close(fd);
        errno = saved_errno;
        return -1;
    }
    return fd;
}

static int gpio_set(serial_port_t *port, bool transmit) {
    const char value = transmit ? '1' : '0';

    if (port->gpio_value_fd < 0) {
        return 0;
    }
    if (lseek(port->gpio_value_fd, 0, SEEK_SET) < 0 ||
        write_full(port->gpio_value_fd, &value, 1U) != 0) {
        return -1;
    }
    return 0;
}

int serial_port_open(serial_port_t *port, const char *device, int baud,
                     int direction_gpio) {
    struct termios settings;
    speed_t speed = baud_to_speed(baud);
    int saved_errno;

    serial_port_reset(port);
    if (speed == (speed_t)0 || copy_string(port->device, sizeof(port->device), device) != 0) {
        errno = EINVAL;
        return -1;
    }
    port->fd = open(device, O_RDWR | O_NOCTTY | O_NONBLOCK | O_CLOEXEC);
    if (port->fd < 0) {
        return -1;
    }
    if (tcgetattr(port->fd, &settings) != 0) {
        goto fail;
    }
    cfmakeraw(&settings);
    settings.c_cflag &= ~(PARENB | CSTOPB | CSIZE | CRTSCTS);
    settings.c_cflag |= CS8 | CLOCAL | CREAD;
    settings.c_cc[VMIN] = 0;
    settings.c_cc[VTIME] = 0;
    if (cfsetispeed(&settings, speed) != 0 || cfsetospeed(&settings, speed) != 0 ||
        tcsetattr(port->fd, TCSANOW, &settings) != 0) {
        goto fail;
    }
    if (tcflush(port->fd, TCIOFLUSH) != 0) {
        goto fail;
    }
    port->baud = baud;
    port->direction_gpio = direction_gpio;
    if (direction_gpio >= 0) {
        port->gpio_value_fd = gpio_prepare(direction_gpio);
        if (port->gpio_value_fd < 0) {
            goto fail;
        }
    }
    return 0;

fail:
    saved_errno = errno;
    serial_port_close(port);
    errno = saved_errno;
    return -1;
}

void serial_port_close(serial_port_t *port) {
    if (port == NULL) {
        return;
    }
    if (port->gpio_value_fd >= 0) {
        (void)gpio_set(port, false);
        (void)close(port->gpio_value_fd);
    }
    if (port->fd >= 0) {
        (void)close(port->fd);
    }
    serial_port_reset(port);
}

static int poll_wait(int fd, short events, unsigned int timeout_ms,
                     const volatile sig_atomic_t *stop_flag) {
    struct pollfd descriptor = {.fd = fd, .events = events, .revents = 0};

    while (stop_flag == NULL || *stop_flag == 0) {
        int result = poll(&descriptor, 1, (int)timeout_ms);
        if (result >= 0) {
            if (result > 0 && (descriptor.revents & (POLLERR | POLLHUP | POLLNVAL)) != 0) {
                errno = EIO;
                return -1;
            }
            return result;
        }
        if (errno != EINTR) {
            return -1;
        }
    }
    errno = EINTR;
    return -1;
}

static int serial_write_request(serial_port_t *port, const uint8_t *request,
                                size_t request_length,
                                const volatile sig_atomic_t *stop_flag) {
    size_t offset = 0;

    while (offset < request_length) {
        ssize_t written = write(port->fd, request + offset, request_length - offset);
        if (written > 0) {
            offset += (size_t)written;
            continue;
        }
        if (written < 0 && errno == EINTR) {
            continue;
        }
        if (written < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            if (poll_wait(port->fd, POLLOUT, 1000U, stop_flag) > 0) {
                continue;
            }
        }
        if (written == 0) {
            errno = EIO;
        }
        return -1;
    }
    return 0;
}

static unsigned int remaining_ms(uint64_t deadline_ns) {
    uint64_t now = clock_now_ns(CLOCK_MONOTONIC);
    uint64_t remaining;

    if (now >= deadline_ns) {
        return 0U;
    }
    remaining = deadline_ns - now;
    remaining = (remaining + 999999U) / 1000000U;
    return remaining > UINT32_MAX ? UINT32_MAX : (unsigned int)remaining;
}

int serial_transaction(serial_port_t *port,
                       const uint8_t *request, size_t request_length,
                       uint8_t *response, size_t response_capacity,
                       unsigned int timeout_ms, unsigned int quiet_ms,
                       size_t expected_length, int terminator_byte,
                       const volatile sig_atomic_t *stop_flag,
                       serial_result_t *result) {
    uint64_t deadline_ns;

    memset(result, 0, sizeof(*result));
    if (port == NULL || port->fd < 0 || request == NULL || response == NULL ||
        response_capacity == 0U) {
        errno = EINVAL;
        result->system_errno = errno;
        return -1;
    }
    if (tcflush(port->fd, TCIFLUSH) != 0 || gpio_set(port, true) != 0 ||
        serial_write_request(port, request, request_length, stop_flag) != 0 ||
        tcdrain(port->fd) != 0 || gpio_set(port, false) != 0) {
        result->system_errno = errno;
        (void)gpio_set(port, false);
        return -1;
    }

    deadline_ns = clock_now_ns(CLOCK_MONOTONIC) + (uint64_t)timeout_ms * 1000000ULL;
    while ((stop_flag == NULL || *stop_flag == 0) && result->response_length < response_capacity) {
        unsigned int wait_ms = remaining_ms(deadline_ns);
        int ready;
        ssize_t count;

        if (wait_ms == 0U) {
            break;
        }
        if (result->response_length > 0U && quiet_ms > 0U && wait_ms > quiet_ms) {
            wait_ms = quiet_ms;
        }
        ready = poll_wait(port->fd, POLLIN, wait_ms, stop_flag);
        if (ready < 0) {
            result->system_errno = errno;
            return -1;
        }
        if (ready == 0) {
            if (result->response_length == 0U) {
                result->timed_out = true;
            }
            break;
        }
        count = read(port->fd, response + result->response_length,
                     response_capacity - result->response_length);
        if (count > 0) {
            bool terminated = terminator_byte >= 0 &&
                              memchr(response + result->response_length,
                                     terminator_byte, (size_t)count) != NULL;
            result->response_length += (size_t)count;
            result->terminated = result->terminated || terminated;
            if ((expected_length > 0U && result->response_length >= expected_length) ||
                terminated) {
                break;
            }
            continue;
        }
        if (count < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) {
            continue;
        }
        if (count == 0) {
            continue;
        }
        result->system_errno = errno;
        return -1;
    }
    if (result->response_length == response_capacity) {
        result->truncated = true;
    }
    if (stop_flag != NULL && *stop_flag != 0 && result->response_length == 0U) {
        result->system_errno = EINTR;
        errno = EINTR;
        return -1;
    }
    return 0;
}
