CC ?= cc
CPPFLAGS ?= -D_POSIX_C_SOURCE=200809L -D_XOPEN_SOURCE=700 -D_DEFAULT_SOURCE -Isrc
CFLAGS ?= -std=c11 -O2 -g -Wall -Wextra -Wpedantic -Werror
LDFLAGS ?=
LDLIBS ?= -pthread -lm

TARGET := bin/aws-reader
SRC := \
	src/main.c \
	src/config.c \
	src/protocol.c \
	src/reader.c \
	src/serial_io.c \
	src/storage.c \
	src/systemd_notify.c \
	src/util.c
OBJ := $(SRC:src/%.c=obj/%.o)

TEST_PROTOCOL := bin/test-protocol
TEST_CONFIG := bin/test-config

.PHONY: all clean test install

all: $(TARGET)

$(TARGET): $(OBJ) | bin
	$(CC) $(LDFLAGS) -o $@ $(OBJ) $(LDLIBS)

obj/%.o: src/%.c | obj
	$(CC) $(CPPFLAGS) $(CFLAGS) -c -o $@ $<

$(TEST_PROTOCOL): tests/test_protocol.c src/protocol.c src/protocol.h | bin
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ tests/test_protocol.c src/protocol.c -lm

$(TEST_CONFIG): tests/test_config.c src/config.c src/config.h src/util.c src/util.h | bin
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ tests/test_config.c src/config.c src/util.c -pthread

obj bin:
	mkdir -p $@

test: $(TARGET) $(TEST_PROTOCOL) $(TEST_CONFIG)
	./$(TEST_PROTOCOL)
	./$(TEST_CONFIG)
	python3 tests/integration_mock.py ./$(TARGET)
	python3 tests/test_health_checker.py tools/check_data_health.py
	python3 tests/test_offline_decoder.py tools/decode_raw_data.py

install: $(TARGET)
	install -D -m 0755 $(TARGET) $(DESTDIR)/usr/local/bin/aws-reader
	install -D -m 0755 tools/check_data_health.py $(DESTDIR)/usr/local/lib/aws-reader/check-data-health
	install -D -m 0644 deploy/aws-reader.service $(DESTDIR)/etc/systemd/system/aws-reader.service
	install -D -m 0644 deploy/aws-reader-health.service $(DESTDIR)/etc/systemd/system/aws-reader-health.service
	install -D -m 0644 deploy/aws-reader-health.timer $(DESTDIR)/etc/systemd/system/aws-reader-health.timer
	@if [ ! -e "$(DESTDIR)/etc/aws-reader.conf" ]; then \
		install -D -m 0640 config/aws-reader.conf.example "$(DESTDIR)/etc/aws-reader.conf"; \
	else \
		printf '%s\n' 'Keeping existing $(DESTDIR)/etc/aws-reader.conf'; \
	fi

clean:
	rm -rf obj bin test-output
