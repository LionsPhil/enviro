"""PIO-based watchdog timer with unclean shutdown detection.

The core implementation of this is Julia7676's work in
https://github.com/pimoroni/enviro/pull/144

It is expected you arm() the watchdog as early as possible, so checking for
a previous (un)clean shutdown and marking this run as dirty() can then be
done afterwards."""

import os

from machine import Pin
from rp2 import PIO, StateMachine, asm_pio

from enviro import helpers
from enviro.constants import HOLD_VSYS_EN_PIN
from phew import logging

STATE_MACHINE_ID = 0
DIRTY_FILE = "dirty.txt"

# Ref: https://docs.micropython.org/en/latest/library/rp2.html#module-rp2

# This is the tiny little delay loop that runs in the PIO controller.
# The side()-effect of the 'done' loop is to pull the power latch low.
@asm_pio(sideset_init=PIO.OUT_HIGH)
def _delayoff_prog():
    label('d_loop')
    jmp(y_dec, 'd_loop') [1]
    label('done')
    jmp('done').side(0)


def arm(minutes: int) -> None:
    """Arm the watchdog to cut power in a number of minutes.

    This will do nothing on USB. It can be reset, but cannot be disarmed."""
    delay_ms = int(minutes * 60 * 1000)
    sm = StateMachine(STATE_MACHINE_ID, _delayoff_prog, freq=2000,
                            sideset_base=Pin(HOLD_VSYS_EN_PIN))
    sm.put(delay_ms)
    sm.exec('pull()')
    sm.exec("mov(y, osr)") # Load max count into Y.
    sm.active(1)
    logging.debug(f'> Watchdog set for {minutes} minutes')


def clean() -> bool:
    """Detect if we last shut down gracefully."""
    return not helpers.file_exists(DIRTY_FILE)


def dirty() -> None:
    """Mark that we need to shut down gracefully."""
    try:
        with open(DIRTY_FILE, "w") as dirtyfile:
            dirtyfile.write("")
    except OSError as e:
        logging.error("!  could not touch dirty file: {e}")


def cleanse() -> None:
    """Mark that we are shutting down gracefully."""
    try:
        os.remove(DIRTY_FILE)
    except OSError as e:
        logging.error("!  could not remove dirty file: {e}")
