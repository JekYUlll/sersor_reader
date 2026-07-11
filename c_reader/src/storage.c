#include "storage.h"

#include "util.h"

#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <ftw.h>
#include <limits.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

typedef struct compression_job {
    char path[PATH_MAX];
    struct compression_job *next;
} compression_job_t;

struct storage_manager {
    char data_dir[PATH_MAX];
    char session_slug[64];
    uint64_t rotate_ns;
    uint64_t sync_ns;
    uint64_t min_free_bytes;
    bool compression_enabled;

    pthread_mutex_t queue_mutex;
    pthread_cond_t queue_condition;
    compression_job_t *queue_head;
    compression_job_t *queue_tail;
    bool stopping;
    bool compressor_started;
    pthread_t compressor_thread;

    pthread_mutex_t space_mutex;
    uint64_t last_space_check_ns;
    bool space_state_known;
    bool space_available;
};

struct storage_logger {
    storage_manager_t *manager;
    char category[32];
    char name[64];
    char active_path[PATH_MAX];
    int fd;
    unsigned int part;
    uint64_t opened_monotonic_ns;
    uint64_t last_sync_ns;
    pthread_mutex_t mutex;
};

static storage_manager_t *recovery_manager;

int storage_mountpoint_is_mounted(const char *mountpoint) {
    FILE *file;
    char *line = NULL;
    size_t capacity = 0;
    int found = 0;

    if (mountpoint == NULL || mountpoint[0] == '\0') {
        return 1;
    }
    file = fopen("/proc/self/mountinfo", "r");
    if (file == NULL) {
        return -1;
    }
    while (getline(&line, &capacity, file) >= 0) {
        char field1[128];
        char field2[128];
        char field3[128];
        char field4[PATH_MAX];
        char field5[PATH_MAX];
        int count = sscanf(line, "%127s %127s %127s %4095s %4095s",
                           field1, field2, field3, field4, field5);
        if (count == 5 && strcmp(field5, mountpoint) == 0) {
            found = 1;
            break;
        }
    }
    free(line);
    (void)fclose(file);
    return found;
}

static void make_session_slug(char *output, size_t output_size, const char *session_id) {
    size_t source_index;
    size_t output_index = 0;

    for (source_index = 0; session_id[source_index] != '\0' && output_index + 1U < output_size;
         ++source_index) {
        unsigned char value = (unsigned char)session_id[source_index];
        if (isalnum(value) != 0 || value == '-' || value == '_') {
            output[output_index++] = (char)value;
        }
    }
    if (output_index == 0U && output_size > 1U) {
        output[output_index++] = 's';
    }
    output[output_index] = '\0';
}

static int queue_compression(storage_manager_t *manager, const char *path) {
    compression_job_t *job;

    if (!manager->compression_enabled) {
        return 0;
    }
    job = calloc(1U, sizeof(*job));
    if (job == NULL || copy_string(job->path, sizeof(job->path), path) != 0) {
        free(job);
        return -1;
    }
    (void)pthread_mutex_lock(&manager->queue_mutex);
    if (manager->queue_tail == NULL) {
        manager->queue_head = job;
    } else {
        manager->queue_tail->next = job;
    }
    manager->queue_tail = job;
    (void)pthread_cond_signal(&manager->queue_condition);
    (void)pthread_mutex_unlock(&manager->queue_mutex);
    return 0;
}

static int child_exit_status(pid_t child) {
    int status;

    while (waitpid(child, &status, 0) < 0) {
        if (errno != EINTR) {
            return -1;
        }
    }
    if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) {
        errno = EIO;
        return -1;
    }
    return 0;
}

static int run_gzip(const char *source, const char *temporary_path) {
    int output_fd = open(temporary_path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0640);
    pid_t child;
    int result;
    int saved_errno;

    if (output_fd < 0) {
        return -1;
    }
    child = fork();
    if (child == 0) {
        if (dup2(output_fd, STDOUT_FILENO) < 0) {
            _exit(126);
        }
        (void)close(output_fd);
        execlp("gzip", "gzip", "-n", "-c", "--", source, (char *)NULL);
        _exit(127);
    }
    if (child < 0) {
        saved_errno = errno;
        (void)close(output_fd);
        (void)unlink(temporary_path);
        errno = saved_errno;
        return -1;
    }
    result = child_exit_status(child);
    saved_errno = errno;
    if (result == 0 && fsync(output_fd) != 0) {
        result = -1;
        saved_errno = errno;
    }
    if (close(output_fd) != 0 && result == 0) {
        result = -1;
        saved_errno = errno;
    }
    if (result != 0) {
        (void)unlink(temporary_path);
    }
    errno = saved_errno;
    return result;
}

static int split_path(const char *path, char *directory, size_t directory_size,
                      char *basename, size_t basename_size) {
    const char *slash = strrchr(path, '/');
    size_t directory_length;

    if (slash == NULL) {
        return copy_string(directory, directory_size, ".") == 0 &&
                       copy_string(basename, basename_size, path) == 0
                   ? 0
                   : -1;
    }
    directory_length = slash == path ? 1U : (size_t)(slash - path);
    if (directory_length >= directory_size ||
        strlen(slash + 1) >= basename_size) {
        errno = ENAMETOOLONG;
        return -1;
    }
    memcpy(directory, path, directory_length);
    directory[directory_length] = '\0';
    return copy_string(basename, basename_size, slash + 1);
}

static int run_sha256(const char *compressed_path, const char *temporary_path) {
    char directory[PATH_MAX];
    char basename[PATH_MAX];
    int output_fd;
    pid_t child;
    int result;
    int saved_errno;

    if (split_path(compressed_path, directory, sizeof(directory), basename, sizeof(basename)) != 0) {
        return -1;
    }
    output_fd = open(temporary_path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0640);
    if (output_fd < 0) {
        return -1;
    }
    child = fork();
    if (child == 0) {
        if (chdir(directory) != 0 || dup2(output_fd, STDOUT_FILENO) < 0) {
            _exit(126);
        }
        (void)close(output_fd);
        execlp("sha256sum", "sha256sum", "--", basename, (char *)NULL);
        _exit(127);
    }
    if (child < 0) {
        saved_errno = errno;
        (void)close(output_fd);
        (void)unlink(temporary_path);
        errno = saved_errno;
        return -1;
    }
    result = child_exit_status(child);
    saved_errno = errno;
    if (result == 0 && fsync(output_fd) != 0) {
        result = -1;
        saved_errno = errno;
    }
    if (close(output_fd) != 0 && result == 0) {
        result = -1;
        saved_errno = errno;
    }
    if (result != 0) {
        (void)unlink(temporary_path);
    }
    errno = saved_errno;
    return result;
}

static int verify_sha256(const char *sidecar_path) {
    char directory[PATH_MAX];
    char basename[PATH_MAX];
    int null_fd;
    pid_t child;

    if (split_path(sidecar_path, directory, sizeof(directory), basename, sizeof(basename)) != 0) {
        return -1;
    }
    null_fd = open("/dev/null", O_WRONLY | O_CLOEXEC);
    if (null_fd < 0) {
        return -1;
    }
    child = fork();
    if (child == 0) {
        if (chdir(directory) != 0 || dup2(null_fd, STDOUT_FILENO) < 0 ||
            dup2(null_fd, STDERR_FILENO) < 0) {
            _exit(126);
        }
        (void)close(null_fd);
        execlp("sha256sum", "sha256sum", "-c", "--status", "--", basename, (char *)NULL);
        _exit(127);
    }
    (void)close(null_fd);
    if (child < 0) {
        return -1;
    }
    return child_exit_status(child);
}

static int compress_file(const char *source) {
    char compressed[PATH_MAX];
    char compressed_temp[PATH_MAX];
    char sidecar[PATH_MAX];
    char sidecar_temp[PATH_MAX];
    long pid = (long)getpid();

    if (snprintf(compressed, sizeof(compressed), "%s.gz", source) >= (int)sizeof(compressed) ||
        snprintf(compressed_temp, sizeof(compressed_temp), "%s.tmp.%ld", compressed, pid) >=
            (int)sizeof(compressed_temp) ||
        snprintf(sidecar, sizeof(sidecar), "%s.sha256", compressed) >= (int)sizeof(sidecar) ||
        snprintf(sidecar_temp, sizeof(sidecar_temp), "%s.tmp.%ld", sidecar, pid) >=
            (int)sizeof(sidecar_temp)) {
        errno = ENAMETOOLONG;
        return -1;
    }

    if (access(compressed, F_OK) == 0 && access(sidecar, F_OK) == 0) {
        if (verify_sha256(sidecar) == 0) {
            if (unlink(source) != 0 && errno != ENOENT) {
                return -1;
            }
            (void)fsync_parent_directory(source);
            return 0;
        }
        app_log("ERROR", "existing archive failed SHA-256 verification: %s", compressed);
        return -1;
    }
    if (access(compressed, F_OK) == 0 && access(sidecar, F_OK) != 0) {
        if (unlink(compressed) != 0) {
            return -1;
        }
    }
    (void)unlink(compressed_temp);
    (void)unlink(sidecar_temp);
    if (run_gzip(source, compressed_temp) != 0 ||
        rename(compressed_temp, compressed) != 0 ||
        fsync_parent_directory(compressed) != 0) {
        int saved_errno = errno;
        (void)unlink(compressed_temp);
        errno = saved_errno;
        return -1;
    }
    if (run_sha256(compressed, sidecar_temp) != 0 ||
        rename(sidecar_temp, sidecar) != 0 ||
        fsync_parent_directory(sidecar) != 0) {
        int saved_errno = errno;
        (void)unlink(sidecar_temp);
        errno = saved_errno;
        return -1;
    }
    if (unlink(source) != 0 || fsync_parent_directory(source) != 0) {
        return -1;
    }
    app_log("INFO", "archive ready: %s", compressed);
    return 0;
}

static void *compressor_main(void *argument) {
    storage_manager_t *manager = argument;

    for (;;) {
        compression_job_t *job;

        (void)pthread_mutex_lock(&manager->queue_mutex);
        while (manager->queue_head == NULL && !manager->stopping) {
            (void)pthread_cond_wait(&manager->queue_condition, &manager->queue_mutex);
        }
        if (manager->queue_head == NULL && manager->stopping) {
            (void)pthread_mutex_unlock(&manager->queue_mutex);
            break;
        }
        job = manager->queue_head;
        manager->queue_head = job->next;
        if (manager->queue_head == NULL) {
            manager->queue_tail = NULL;
        }
        (void)pthread_mutex_unlock(&manager->queue_mutex);

        if (compress_file(job->path) != 0) {
            app_log("ERROR", "compression kept source %s: %s", job->path, strerror(errno));
        }
        free(job);
    }
    return NULL;
}

static int recover_callback(const char *path, const struct stat *stat_buffer,
                            int type_flag, struct FTW *ftw_buffer) {
    storage_manager_t *manager = recovery_manager;
    (void)stat_buffer;
    (void)ftw_buffer;

    if (type_flag != FTW_F || manager == NULL) {
        return 0;
    }
    if (string_ends_with(path, ".jsonl.active")) {
        char recovered[PATH_MAX];
        size_t active_suffix_length = strlen(".active");
        size_t path_length = strlen(path);

        if (path_length - active_suffix_length >= sizeof(recovered)) {
            app_log("ERROR", "recovery path is too long: %s", path);
            return 0;
        }
        memcpy(recovered, path, path_length - active_suffix_length);
        recovered[path_length - active_suffix_length] = '\0';
        if (access(recovered, F_OK) == 0) {
            size_t stem_length = path_length - strlen(".jsonl.active");
            if (snprintf(recovered, sizeof(recovered), "%.*s.recovered-%ld.jsonl",
                         (int)stem_length, path, (long)getpid()) >= (int)sizeof(recovered)) {
                app_log("ERROR", "recovery path is too long: %s", path);
                return 0;
            }
        }
        if (rename(path, recovered) == 0) {
            (void)fsync_parent_directory(recovered);
            app_log("WARN", "sealed interrupted active file: %s", recovered);
            if (queue_compression(manager, recovered) != 0) {
                app_log("ERROR", "cannot queue recovered file %s", recovered);
            }
        } else {
            app_log("ERROR", "cannot seal interrupted file %s: %s", path, strerror(errno));
        }
    } else if (string_ends_with(path, ".jsonl")) {
        if (queue_compression(manager, path) != 0) {
            app_log("ERROR", "cannot queue pending file %s", path);
        }
    }
    return 0;
}

storage_manager_t *storage_manager_create(const reader_config_t *config,
                                          const char *session_id) {
    storage_manager_t *manager = calloc(1U, sizeof(*manager));
    char raw_dir[PATH_MAX];
    char health_dir[PATH_MAX];

    if (manager == NULL) {
        return NULL;
    }
    if (copy_string(manager->data_dir, sizeof(manager->data_dir), config->data_dir) != 0) {
        goto fail;
    }
    make_session_slug(manager->session_slug, sizeof(manager->session_slug), session_id);
    manager->rotate_ns = (uint64_t)config->rotate_minutes * 60ULL * 1000000000ULL;
    manager->sync_ns = (uint64_t)config->sync_interval_sec * 1000000000ULL;
    manager->min_free_bytes = config->min_free_mb * 1024ULL * 1024ULL;
    manager->compression_enabled = config->compression_enabled;

    if (mkdir_p(manager->data_dir, 0750U) != 0 ||
        snprintf(raw_dir, sizeof(raw_dir), "%s/raw", manager->data_dir) >= (int)sizeof(raw_dir) ||
        snprintf(health_dir, sizeof(health_dir), "%s/health", manager->data_dir) >=
            (int)sizeof(health_dir) ||
        mkdir_p(raw_dir, 0750U) != 0 || mkdir_p(health_dir, 0750U) != 0) {
        goto fail;
    }
    if (pthread_mutex_init(&manager->queue_mutex, NULL) != 0 ||
        pthread_cond_init(&manager->queue_condition, NULL) != 0 ||
        pthread_mutex_init(&manager->space_mutex, NULL) != 0) {
        errno = EAGAIN;
        goto fail;
    }
    if (manager->compression_enabled) {
        if (pthread_create(&manager->compressor_thread, NULL, compressor_main, manager) != 0) {
            errno = EAGAIN;
            goto fail_sync;
        }
        manager->compressor_started = true;
    }

    recovery_manager = manager;
    if (nftw(manager->data_dir, recover_callback, 16, FTW_PHYS) != 0) {
        app_log("ERROR", "startup recovery scan failed for %s: %s",
                manager->data_dir, strerror(errno));
    }
    recovery_manager = NULL;
    return manager;

fail_sync:
    (void)pthread_mutex_destroy(&manager->space_mutex);
    (void)pthread_cond_destroy(&manager->queue_condition);
    (void)pthread_mutex_destroy(&manager->queue_mutex);
fail:
    free(manager);
    return NULL;
}

void storage_manager_destroy(storage_manager_t *manager) {
    compression_job_t *job;

    if (manager == NULL) {
        return;
    }
    if (manager->compressor_started) {
        (void)pthread_mutex_lock(&manager->queue_mutex);
        manager->stopping = true;
        (void)pthread_cond_broadcast(&manager->queue_condition);
        (void)pthread_mutex_unlock(&manager->queue_mutex);
        (void)pthread_join(manager->compressor_thread, NULL);
    }
    while ((job = manager->queue_head) != NULL) {
        manager->queue_head = job->next;
        free(job);
    }
    (void)pthread_mutex_destroy(&manager->space_mutex);
    (void)pthread_cond_destroy(&manager->queue_condition);
    (void)pthread_mutex_destroy(&manager->queue_mutex);
    free(manager);
}

storage_logger_t *storage_logger_create(storage_manager_t *manager,
                                        const char *category,
                                        const char *name) {
    storage_logger_t *logger = calloc(1U, sizeof(*logger));

    if (logger == NULL) {
        return NULL;
    }
    logger->manager = manager;
    logger->fd = -1;
    if (copy_string(logger->category, sizeof(logger->category), category) != 0 ||
        copy_string(logger->name, sizeof(logger->name), name) != 0 ||
        pthread_mutex_init(&logger->mutex, NULL) != 0) {
        free(logger);
        errno = EINVAL;
        return NULL;
    }
    return logger;
}

static bool manager_has_space(storage_manager_t *manager, uint64_t monotonic_ns) {
    struct statvfs stats;
    bool available;
    uint64_t free_bytes;

    (void)pthread_mutex_lock(&manager->space_mutex);
    if (manager->space_state_known &&
        monotonic_ns >= manager->last_space_check_ns &&
        monotonic_ns - manager->last_space_check_ns < 5000000000ULL) {
        available = manager->space_available;
        (void)pthread_mutex_unlock(&manager->space_mutex);
        return available;
    }
    if (statvfs(manager->data_dir, &stats) != 0) {
        available = false;
        app_log("ERROR", "cannot inspect free space at %s: %s",
                manager->data_dir, strerror(errno));
    } else {
        if ((uint64_t)stats.f_bavail > UINT64_MAX / (uint64_t)stats.f_frsize) {
            free_bytes = UINT64_MAX;
        } else {
            free_bytes = (uint64_t)stats.f_bavail * (uint64_t)stats.f_frsize;
        }
        available = free_bytes >= manager->min_free_bytes;
    }
    if (!manager->space_state_known || available != manager->space_available) {
        app_log(available ? "INFO" : "ERROR",
                available ? "data disk has writable free space" :
                            "data disk is below free-space threshold; writes paused");
    }
    manager->space_available = available;
    manager->space_state_known = true;
    manager->last_space_check_ns = monotonic_ns;
    (void)pthread_mutex_unlock(&manager->space_mutex);
    return available;
}

static int logger_open_file(storage_logger_t *logger, uint64_t monotonic_ns) {
    struct timespec realtime;
    struct tm utc;
    char date[16];
    char timestamp[32];
    char directory[PATH_MAX];
    int flags = O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC;

    if (clock_gettime(CLOCK_REALTIME, &realtime) != 0 ||
        gmtime_r(&realtime.tv_sec, &utc) == NULL ||
        strftime(date, sizeof(date), "%Y%m%d", &utc) == 0 ||
        strftime(timestamp, sizeof(timestamp), "%Y%m%dT%H%M%SZ", &utc) == 0) {
        return -1;
    }
    if (snprintf(directory, sizeof(directory), "%s/%s/%s", logger->manager->data_dir,
                 logger->category, date) >= (int)sizeof(directory) ||
        mkdir_p(directory, 0750U) != 0) {
        return -1;
    }
    ++logger->part;
    if (snprintf(logger->active_path, sizeof(logger->active_path),
                 "%s/%s_%s_%s_part%04u.jsonl.active", directory, timestamp,
                 logger->name, logger->manager->session_slug, logger->part) >=
        (int)sizeof(logger->active_path)) {
        errno = ENAMETOOLONG;
        return -1;
    }
    logger->fd = open(logger->active_path, flags, 0640);
    if (logger->fd < 0) {
        return -1;
    }
    logger->opened_monotonic_ns = monotonic_ns;
    logger->last_sync_ns = monotonic_ns;
    app_log("INFO", "opened %s log %s", logger->name, logger->active_path);
    return 0;
}

static int logger_seal_file(storage_logger_t *logger) {
    char sealed[PATH_MAX];
    size_t path_length;
    size_t suffix_length = strlen(".active");
    int result = 0;

    if (logger->fd < 0) {
        return 0;
    }
    if (fdatasync(logger->fd) != 0) {
        result = -1;
    }
    if (close(logger->fd) != 0) {
        result = -1;
    }
    logger->fd = -1;
    path_length = strlen(logger->active_path);
    if (path_length <= suffix_length || path_length - suffix_length >= sizeof(sealed)) {
        errno = ENAMETOOLONG;
        return -1;
    }
    memcpy(sealed, logger->active_path, path_length - suffix_length);
    sealed[path_length - suffix_length] = '\0';
    if (rename(logger->active_path, sealed) != 0 || fsync_parent_directory(sealed) != 0) {
        return -1;
    }
    logger->active_path[0] = '\0';
    if (queue_compression(logger->manager, sealed) != 0) {
        app_log("ERROR", "cannot queue sealed log %s", sealed);
        result = -1;
    }
    return result;
}

storage_write_result_t storage_logger_write(storage_logger_t *logger,
                                            const char *json_line,
                                            uint64_t monotonic_ns) {
    storage_write_result_t result = STORAGE_WRITE_OK;
    size_t line_length = strlen(json_line);

    if (!manager_has_space(logger->manager, monotonic_ns)) {
        return STORAGE_WRITE_LOW_SPACE;
    }
    (void)pthread_mutex_lock(&logger->mutex);
    if (logger->fd >= 0 &&
        monotonic_ns >= logger->opened_monotonic_ns &&
        monotonic_ns - logger->opened_monotonic_ns >= logger->manager->rotate_ns &&
        logger_seal_file(logger) != 0) {
        app_log("ERROR", "cannot rotate %s log: %s", logger->name, strerror(errno));
        result = STORAGE_WRITE_ERROR;
        goto done;
    }
    if (logger->fd < 0 && logger_open_file(logger, monotonic_ns) != 0) {
        app_log("ERROR", "cannot open %s log: %s", logger->name, strerror(errno));
        result = STORAGE_WRITE_ERROR;
        goto done;
    }
    if (write_full(logger->fd, json_line, line_length) != 0 ||
        write_full(logger->fd, "\n", 1U) != 0) {
        app_log("ERROR", "cannot append %s log: %s", logger->name, strerror(errno));
        (void)close(logger->fd);
        logger->fd = -1;
        result = STORAGE_WRITE_ERROR;
        goto done;
    }
    if (monotonic_ns >= logger->last_sync_ns &&
        monotonic_ns - logger->last_sync_ns >= logger->manager->sync_ns) {
        if (fdatasync(logger->fd) != 0) {
            app_log("ERROR", "cannot sync %s log: %s", logger->name, strerror(errno));
            result = STORAGE_WRITE_ERROR;
            goto done;
        }
        logger->last_sync_ns = monotonic_ns;
    }

done:
    (void)pthread_mutex_unlock(&logger->mutex);
    return result;
}

void storage_logger_destroy(storage_logger_t *logger) {
    if (logger == NULL) {
        return;
    }
    (void)pthread_mutex_lock(&logger->mutex);
    if (logger_seal_file(logger) != 0) {
        app_log("ERROR", "cannot seal %s log during shutdown: %s",
                logger->name, strerror(errno));
    }
    (void)pthread_mutex_unlock(&logger->mutex);
    (void)pthread_mutex_destroy(&logger->mutex);
    free(logger);
}
