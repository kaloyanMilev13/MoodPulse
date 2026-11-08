.RECIPEPREFIX := >

BOARD := Seeeduino:nrf52:xiaonRF52840Sense
PORT ?= /dev/ttyACM0

.PHONY: build upload monitor

build:
>arduino-cli compile --fqbn $(BOARD) .

upload: build
>arduino-cli upload --port $(PORT) --fqbn $(BOARD) .

monitor:
>arduino-cli monitor --port $(PORT) --config baudrate=115200
