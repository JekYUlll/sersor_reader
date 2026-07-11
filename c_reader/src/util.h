#ifndef AWS_READER_UTIL_H
#define AWS_READER_UTIL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <time.h>

typedef struct {
    char *data;
    size_t len;
    size_t cap;
} strbuf_t;

uint64_t clock_now_ns(clockid_t clock_id);
void format_iso8601_utc(const struct timespec *ts, char *out, size_t out_size);
void app_log(const char *level, const char *format, ...)
    __attribute__((format(printf, 2, 3)));

int copy_string(char *dest, size_t dest_size, const char *source);
int mkdir_p(const char *path, unsigned int mode);
int write_full(int fd, const void *buffer, size_t length);
int fsync_parent_directory(const char *path);
bool string_ends_with(const char *value, const char *suffix);
char *hex_encode_alloc(const unsigned char *data, size_t length);

int strbuf_init(strbuf_t *buffer, size_t initial_capacity);
void strbuf_free(strbuf_t *buffer);
int strbuf_append(strbuf_t *buffer, const char *text);
int strbuf_append_n(strbuf_t *buffer, const char *text, size_t length);
int strbuf_appendf(strbuf_t *buffer, const char *format, ...)
    __attribute__((format(printf, 2, 3)));
int strbuf_append_json_string(strbuf_t *buffer, const char *text);

#endif
