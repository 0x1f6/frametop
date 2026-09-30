# fh-tracker, the C++ tracker. Needs ncnn built into ../vendor/ncnn/build/install
# (see README.md) and jsoncpp.
NCNN     ?= ../vendor/ncnn/build/install
CXXFLAGS ?= -O2 -g -Wall -Wextra -Wno-unused-parameter
CXXFLAGS += -std=c++17 -fopenmp -I$(NCNN)/include/ncnn
LDLIBS    = $(NCNN)/lib/libncnn.a -ljsoncpp -fopenmp -lpthread

SRC = main.cpp calib.cpp nets.cpp tracker.cpp io.cpp record.cpp
HDR = calib.h geom.h nets.h tracker.h io.h record.h ../camd/fhring.h ../include/fh_hands.h

all: fh-tracker fh-replay fh-ringplay

fh-tracker: $(SRC) $(HDR)
	$(CXX) $(CXXFLAGS) -o $@ $(SRC) $(LDLIBS)

fh-replay: replay.cpp calib.cpp nets.cpp tracker.cpp record.cpp $(HDR)
	$(CXX) $(CXXFLAGS) -o $@ replay.cpp calib.cpp nets.cpp tracker.cpp record.cpp $(LDLIBS)

fh-ringplay: ringplay.cpp record.h ../camd/fhring.h
	$(CXX) $(CXXFLAGS) -o $@ ringplay.cpp

clean:
	rm -f fh-tracker fh-replay fh-ringplay nettest

.PHONY: all clean

nettest: nettest.cpp nets.cpp nets.h geom.h
	$(CXX) $(CXXFLAGS) -o $@ nettest.cpp nets.cpp $(LDLIBS)
