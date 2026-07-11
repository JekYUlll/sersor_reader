#include "config.h"

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void write_temp_config(char *path, size_t path_size, const char *contents) {
    char template_path[] = "/tmp/aws-reader-config-XXXXXX";
    FILE *file;
    int fd = mkstemp(template_path);

    assert(fd >= 0);
    file = fdopen(fd, "w");
    assert(file != NULL);
    assert(fputs(contents, file) >= 0);
    assert(fclose(file) == 0);
    assert(strlen(template_path) < path_size);
    strcpy(path, template_path);
}

int main(void) {
    reader_config_t config;
    char error[256];
    char path[128];

    config_set_defaults(&config);
    write_temp_config(path, sizeof(path),
                      "data_dir=/tmp/aws-test\n"
                      "required_mountpoint=\n"
                      "p2_enabled=false\n"
                      "modbus_register_count=42\n"
                      "compression_enabled=false\n");
    assert(config_load_file(path, &config, error, sizeof(error)) == 0);
    assert(strcmp(config.data_dir, "/tmp/aws-test") == 0);
    assert(config.required_mountpoint[0] == '\0');
    assert(!config.p2.enabled);
    assert(config.modbus.register_count == 42U);
    assert(!config.compression_enabled);
    assert(unlink(path) == 0);

    config_set_defaults(&config);
    write_temp_config(path, sizeof(path), "unknown_option=true\n");
    assert(config_load_file(path, &config, error, sizeof(error)) != 0);
    assert(strstr(error, "unknown key") != NULL);
    assert(unlink(path) == 0);

    config_set_defaults(&config);
    write_temp_config(path, sizeof(path), "modbus_register_count=126\n");
    assert(config_load_file(path, &config, error, sizeof(error)) != 0);
    assert(strstr(error, "modbus") != NULL);
    assert(unlink(path) == 0);

    (void)puts("test_config: OK");
    return 0;
}
