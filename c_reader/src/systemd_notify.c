#include "systemd_notify.h"

#include <errno.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

int systemd_notify_message(const char *message) {
    const char *socket_path = getenv("NOTIFY_SOCKET");
    struct sockaddr_un address;
    socklen_t address_length;
    size_t path_length;
    int fd;
    ssize_t sent;

    if (socket_path == NULL || socket_path[0] == '\0') {
        return 0;
    }
    path_length = strlen(socket_path);
    if (path_length >= sizeof(address.sun_path)) {
        errno = ENAMETOOLONG;
        return -1;
    }
    memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    if (socket_path[0] == '@') {
        address.sun_path[0] = '\0';
        memcpy(address.sun_path + 1, socket_path + 1, path_length - 1U);
        address_length = (socklen_t)(offsetof(struct sockaddr_un, sun_path) + path_length);
    } else {
        memcpy(address.sun_path, socket_path, path_length + 1U);
        address_length = (socklen_t)(offsetof(struct sockaddr_un, sun_path) + path_length + 1U);
    }
    fd = socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (fd < 0) {
        return -1;
    }
    sent = sendto(fd, message, strlen(message), MSG_NOSIGNAL,
                  (const struct sockaddr *)&address, address_length);
    (void)close(fd);
    return sent < 0 ? -1 : 0;
}
