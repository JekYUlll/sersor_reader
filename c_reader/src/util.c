#include "util.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

uint64_t clock_now_ns(clockid_t clock_id) {
    struct timespec ts;
    if (clock_gettime(clock_id, &ts) != 0) {
        return 0;
    }
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

void format_iso8601_utc(const struct timespec *ts, char *out, size_t out_size) {
    struct tm utc;
    char base[32];

    if (out_size == 0) {
        return;
    }
    if (gmtime_r(&ts->tv_sec, &utc) == NULL ||
        strftime(base, sizeof(base), "%Y-%m-%dT%H:%M:%S", &utc) == 0) {
        (void)snprintf(out, out_size, "1970-01-01T00:00:00.000000000Z");
        return;
    }
    (void)snprintf(out, out_size, "%s.%09ldZ", base, ts->tv_nsec);
}

void app_log(const char *level, const char *format, ...) {
    struct timespec ts;
    char timestamp[48];
    va_list args;

    if (clock_gettime(CLOCK_REALTIME, &ts) != 0) {
        ts.tv_sec = 0;
        ts.tv_nsec = 0;
    }
    format_iso8601_utc(&ts, timestamp, sizeof(timestamp));
    (void)fprintf(stderr, "%s [%s] ", timestamp, level);
    va_start(args, format);
    (void)vfprintf(stderr, format, args);
    va_end(args);
    (void)fputc('\n', stderr);
    (void)fflush(stderr);
}

int copy_string(char *dest, size_t dest_size, const char *source) {
    size_t length;

    if (dest == NULL || source == NULL || dest_size == 0) {
        errno = EINVAL;
        return -1;
    }
    length = strlen(source);
    if (length >= dest_size) {
        errno = ENAMETOOLONG;
        return -1;
    }
    memcpy(dest, source, length + 1);
    return 0;
}

int mkdir_p(const char *path, unsigned int mode) {
    char copy[PATH_MAX];
    char *cursor;

    if (path == NULL || path[0] == '\0') {
        errno = EINVAL;
        return -1;
    }
    if (copy_string(copy, sizeof(copy), path) != 0) {
        return -1;
    }

    for (cursor = copy + 1; *cursor != '\0'; ++cursor) {
        if (*cursor != '/') {
            continue;
        }
        *cursor = '\0';
        if (mkdir(copy, (mode_t)mode) != 0 && errno != EEXIST) {
            return -1;
        }
        *cursor = '/';
    }
    if (mkdir(copy, (mode_t)mode) != 0 && errno != EEXIST) {
        return -1;
    }
    return 0;
}

int write_full(int fd, const void *buffer, size_t length) {
    const unsigned char *bytes = buffer;
    size_t offset = 0;

    while (offset < length) {
        ssize_t written = write(fd, bytes + offset, length - offset);
        if (written > 0) {
            offset += (size_t)written;
            continue;
        }
        if (written < 0 && errno == EINTR) {
            continue;
        }
        if (written == 0) {
            errno = EIO;
        }
        return -1;
    }
    return 0;
}

int fsync_parent_directory(const char *path) {
    char parent[PATH_MAX];
    char *slash;
    int fd;
    int result;

    if (copy_string(parent, sizeof(parent), path) != 0) {
        return -1;
    }
    slash = strrchr(parent, '/');
    if (slash == NULL) {
        return 0;
    }
    if (slash == parent) {
        slash[1] = '\0';
    } else {
        *slash = '\0';
    }
    fd = open(parent, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (fd < 0) {
        return -1;
    }
    result = fsync(fd);
    (void)close(fd);
    return result;
}

bool string_ends_with(const char *value, const char *suffix) {
    size_t value_length;
    size_t suffix_length;

    if (value == NULL || suffix == NULL) {
        return false;
    }
    value_length = strlen(value);
    suffix_length = strlen(suffix);
    return value_length >= suffix_length &&
           memcmp(value + value_length - suffix_length, suffix, suffix_length) == 0;
}

char *hex_encode_alloc(const unsigned char *data, size_t length) {
    static const char alphabet[] = "0123456789abcdef";
    char *output;
    size_t index;

    if (length > (SIZE_MAX - 1U) / 2U) {
        errno = EOVERFLOW;
        return NULL;
    }
    output = malloc(length * 2U + 1U);
    if (output == NULL) {
        return NULL;
    }
    for (index = 0; index < length; ++index) {
        output[index * 2U] = alphabet[data[index] >> 4U];
        output[index * 2U + 1U] = alphabet[data[index] & 0x0fU];
    }
    output[length * 2U] = '\0';
    return output;
}

static int strbuf_reserve(strbuf_t *buffer, size_t needed) {
    char *new_data;
    size_t new_capacity;

    if (needed <= buffer->cap) {
        return 0;
    }
    new_capacity = buffer->cap == 0 ? 256U : buffer->cap;
    while (new_capacity < needed) {
        if (new_capacity > SIZE_MAX / 2U) {
            errno = EOVERFLOW;
            return -1;
        }
        new_capacity *= 2U;
    }
    new_data = realloc(buffer->data, new_capacity);
    if (new_data == NULL) {
        return -1;
    }
    buffer->data = new_data;
    buffer->cap = new_capacity;
    return 0;
}

int strbuf_init(strbuf_t *buffer, size_t initial_capacity) {
    if (buffer == NULL) {
        errno = EINVAL;
        return -1;
    }
    memset(buffer, 0, sizeof(*buffer));
    if (initial_capacity == 0) {
        initial_capacity = 256U;
    }
    if (strbuf_reserve(buffer, initial_capacity) != 0) {
        return -1;
    }
    buffer->data[0] = '\0';
    return 0;
}

void strbuf_free(strbuf_t *buffer) {
    if (buffer == NULL) {
        return;
    }
    free(buffer->data);
    memset(buffer, 0, sizeof(*buffer));
}

int strbuf_append_n(strbuf_t *buffer, const char *text, size_t length) {
    if (buffer == NULL || (text == NULL && length != 0)) {
        errno = EINVAL;
        return -1;
    }
    if (length > SIZE_MAX - buffer->len - 1U) {
        errno = EOVERFLOW;
        return -1;
    }
    if (strbuf_reserve(buffer, buffer->len + length + 1U) != 0) {
        return -1;
    }
    if (length != 0) {
        memcpy(buffer->data + buffer->len, text, length);
    }
    buffer->len += length;
    buffer->data[buffer->len] = '\0';
    return 0;
}

int strbuf_append(strbuf_t *buffer, const char *text) {
    return strbuf_append_n(buffer, text, strlen(text));
}

int strbuf_appendf(strbuf_t *buffer, const char *format, ...) {
    va_list args;
    va_list copy;
    int required;

    va_start(args, format);
    va_copy(copy, args);
    required = vsnprintf(NULL, 0, format, copy);
    va_end(copy);
    if (required < 0) {
        va_end(args);
        return -1;
    }
    if ((size_t)required > SIZE_MAX - buffer->len - 1U ||
        strbuf_reserve(buffer, buffer->len + (size_t)required + 1U) != 0) {
        va_end(args);
        return -1;
    }
    (void)vsnprintf(buffer->data + buffer->len, buffer->cap - buffer->len, format, args);
    va_end(args);
    buffer->len += (size_t)required;
    return 0;
}

int strbuf_append_json_string(strbuf_t *buffer, const char *text) {
    const unsigned char *cursor = (const unsigned char *)text;

    if (strbuf_append_n(buffer, "\"", 1U) != 0) {
        return -1;
    }
    while (*cursor != '\0') {
        switch (*cursor) {
            case '\"':
                if (strbuf_append(buffer, "\\\"") != 0) {
                    return -1;
                }
                break;
            case '\\':
                if (strbuf_append(buffer, "\\\\") != 0) {
                    return -1;
                }
                break;
            case '\b':
                if (strbuf_append(buffer, "\\b") != 0) {
                    return -1;
                }
                break;
            case '\f':
                if (strbuf_append(buffer, "\\f") != 0) {
                    return -1;
                }
                break;
            case '\n':
                if (strbuf_append(buffer, "\\n") != 0) {
                    return -1;
                }
                break;
            case '\r':
                if (strbuf_append(buffer, "\\r") != 0) {
                    return -1;
                }
                break;
            case '\t':
                if (strbuf_append(buffer, "\\t") != 0) {
                    return -1;
                }
                break;
            default:
                if (*cursor < 0x20U) {
                    if (strbuf_appendf(buffer, "\\u%04x", (unsigned int)*cursor) != 0) {
                        return -1;
                    }
                } else if (strbuf_append_n(buffer, (const char *)cursor, 1U) != 0) {
                    return -1;
                }
                break;
        }
        ++cursor;
    }
    return strbuf_append_n(buffer, "\"", 1U);
}
