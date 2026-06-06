import os
import struct
import time

import usocket
from pcf85063a import PCF85063A
from pimoroni_i2c import PimoroniI2C
from umachine import RTC

from enviro import helpers
from phew import logging

SYNC_TIMESTAMP_FILE = "lastsync.txt"
SYNC_TOLERANCE_SECS = 24 * 60 * 60
EARLIEST_REASONABLE_YEAR = 2020
FALLBACK_WAKEUP_MINUTES = 30

# Tread with caution.
# Python3 uses a 9-value tuple for parsed times, with DST info:
# https://docs.python.org/3/library/time.html#time.struct_time
# Micropython uses an 8-value tuple which does not:
# https://docs.micropython.org/en/latest/library/time.html#functions
# The pico drivers for both use the datetime_t of the RasPi Pico SDK:
# https://github.com/raspberrypi/pico-sdk/blob/2.2.0/src/common/pico_base_headers/include/pico/types.h#L107
# They are at least mutually intelligble. None are the same as C struct tm.

class clocks:
    """Manage both RTCs having a reliable timesouce, and timers.

    Expected init is to call the timesync_offline(), bring up the wireless and
    call timesync_online() if it returns false.

    set_timer() is used to override the wakeup time."""

    rtc_pico: RTC = None
    rtc_ext: PCF85063A = None


    def __init__(self, i2c: PimoroniI2C):
        self.rtc_pico = RTC()
        self.rtc_ext = PCF85063A(i2c)
        # Refs for this API and chip datasheet:
        # https://github.com/pimoroni/pimoroni-pico/blob/main/drivers/pcf85063a/pcf85063a.cpp
        # https://www.nxp.com/docs/en/data-sheet/PCF85063A.pdf
        if self.read_external().tm_year == 2000:
            # The RTC is either unset, or has not been cleanly reset (this is a
            # zero read at the I2C level). Most likely, it is not running.
            # Cleanly reset it, which will clear the STOP bit, and set a valid
            # month and day (table 7 in the datasheet). This call blocks as
            # needed.
            logging.info(f"  - PCF85063A RTC is returning year 2000; resetting cleanly")
            self.rtc_ext.reset()
        # Disable the alarm.
        self.rtc_ext.unset_alarm()
        self.rtc_ext.clear_alarm_flag()
        self.rtc_ext.enable_alarm_interrupt(False)
        # Configure the timer to go off in case we crash weird.
        self.set_timer(FALLBACK_WAKEUP_MINUTES)


    def timesync_offline(self) -> bool:
        """Attempt to synchronize time offline.
        Returns true if a good timebase is established, false if not possible."""
        # If we haven't synced recently, it's never good enough.
        # This defends against people powering boards via USB for extended
        # periods, so the onboard pico RTC can stay set, but drift.
        last_sync = self.last_sync()

        # Check if the USB host set our clock via Micropython magic.
        # (If we're on non-USB power, it will reset every time we "sleep".)
        pico_dt = self.read_pico()
        pico_timestamp = time.mktime(pico_dt)
        if pico_dt.tm_year > EARLIEST_REASONABLE_YEAR:
           logging.info("-  pico RTC is set, assuming USB timesync")
           if pico_timestamp - last_sync > SYNC_TOLERANCE_SECS:
               logging.info("-  but it has still been too long since sync")
               return False
           # Sync it across to the external RTC.
           self.write_external(pico_dt)
           return True

        # Ok, does the external RTC have a fresh enough time?
        ext_dt = self.read_external()
        ext_timestamp = time.mktime(ext_dt)
        if ext_dt.tm_year > EARLIEST_REASONABLE_YEAR:
            logging.info("-  external RTC is set")
            if ext_timestamp - last_sync > SYNC_TOLERANCE_SECS:
               logging.info("-  but it has still been too long sinc sync")
               return False
            # Sync it across to the internal RTC.
            self.write_pico(ext_dt)
            return True

        # Nope, no good offline timesource.
        return False


    def timesync_online(self, timeserver="ntp.pool.org", timeout=10, tries=3):
        """Sync to an NTP server. Will throw if unable."""
        timestamp = None
        while timestamp is None and tries > 0:
            try:
                timestamp = self.native_ntp(timeserver, timeout)
            except Exception as e:
                logging.warn(f"!  ntp sync failed: {e}")
            tries -= 1
        if timestamp is None:
            raise Exception("unable to synchronize time")
        # Write to both RTCs.
        t = time.gmtime(timestamp)
        self.write_pico(t)
        self.write_external(t)
        # Save the last successful sync time.
        try:
            with open(SYNC_TIMESTAMP_FILE, "w") as syncfile:
                syncfile.write(timestamp)
        except OSError as e:
            logging.warn("!  timesync file unwritable: {e}")


    def set_timer(self, minutes) -> None:
        """Set the timer to fire an interrupt in some number of minutes."""
        # Per datasheet, disable timer while setting new duration.
        self.rtc_ext.unset_timer()
        self.rtc_ext.clear_timer_flag()
        self.rtc_ext.set_timer(minutes, self.rtc_ext.TIMER_TICK_1_OVER_60HZ)
        self.rtc_ext.enable_timer_interrupt(True)


    def last_sync(self) -> int:
        """Return the epoch timestamp of the last online sync, or zero."""
        if not helpers.file_exists(SYNC_TIMESTAMP_FILE):
            return 0
        try:
            with open(SYNC_TIMESTAMP_FILE, "r") as syncfile:
                return int(syncfile.read())
        except (OSError, ValueError) as e:
            logging.warn("!  timesync file unreadable: {e}")
            return 0


    def force_online_sync_next(self) -> None:
        """Force offline syncs to fail until the next online sync."""
        try:
            os.remove(SYNC_TIMESTAMP_FILE)
        except OSError as e:
            logging.error("!  could not remove timesync file: {e}")


    def read_pico(self) -> time.struct_time:
        """Read the integrate Pico RTC as a mostly-correct struct_time."""
        pico_semi_dt = self.rtc_pico.datetime()
        return pico_semi_dt[:7] + (1,) # cut "subseconds"; add yday


    def write_pico(self, t: time.struct_time) -> None:
        """Write the integrated Pico RTC from a struct_time."""
        self.rtc_pico.datetime((t.tm_year, t.tm_mon, t.tm_mday, t.tm_wday,
                                t.tm_hour, t.tm_min, t.tm_sec, 0))


    def read_external(self) -> time.struct_time:
        """Read the PCF85063A RTC as a mostly-correct struct_time."""
        return self.rtc_ext.datetime() + (1,) # add yday


    def write_external(self, t: time.struct_time) -> None:
        """Write the PCF85063A RTC from a struct_time."""
        self.rtc_ext.datetime((t.tm_year, t.tm_mon, t.tm_mday, t.tm_wday,
                               t.tm_hour, t.tm_min, t.tm_sec, 0))


    # phew's ntp client has no error reporting at all to log or inform retries.
    # (It would be better to make it raise, but then all callers need updating.)
    def native_ntp(timeserver: str, timeout: int) -> int:
        """One-shot NTP sync, returning an epoch timestamp, or throwing."""
        query = bytearray(48)
        query[0] = 0x1b
        logging.debug(f"  - ntp resolve {timeserver}...")
        address = usocket.getaddrinfo(timeserver, 123)[0][-1]
        socket = usocket.socket(usocket.AF_INET, usocket.SOCK_DGRAM)
        socket.settimeout(timeout)
        logging.debug(f"  - ntp send to {address}...")
        socket.sendto(query, address)
        data = socket.recv(48)
        socket.close()
        local_epoch = 2208988800 # selected by Chris - blame him. :-D
        return struct.unpack("!I", data[40:44])[0] - local_epoch
