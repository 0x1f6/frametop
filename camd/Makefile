CFLAGS ?= -O2 -g -Wall -Wextra -Wno-unused-parameter
LDLIBS  = -lm

all: fh-camprobe fh-camd

fh-camprobe: camprobe.c tp.c xrcams.c tp.h xrcams.h
	$(CC) $(CFLAGS) -o $@ camprobe.c tp.c xrcams.c $(LDLIBS)

fh-camd: camd.c tp.c xrcams.c tp.h xrcams.h fhring.h
	$(CC) $(CFLAGS) -o $@ camd.c tp.c xrcams.c $(LDLIBS)

clean:
	rm -f fh-camprobe fh-camd

.PHONY: all clean
