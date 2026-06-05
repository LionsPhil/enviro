# keep the power rail alive by holding VSYS_EN high as early as possible
# ===========================================================================
from enviro.constants import *
from machine import Pin

hold_vsys_en_pin = Pin(HOLD_VSYS_EN_PIN, Pin.OUT, value=True)

# detect board model based on devices on the i2c bus and pin state
# ===========================================================================
from pimoroni_i2c import PimoroniI2C
i2c = PimoroniI2C(I2C_SDA_PIN, I2C_SCL_PIN, 100000)
i2c_devices = i2c.scan()
model = None
if 56 in i2c_devices: # 56 = colour / light sensor and only present on Indoor
  model = "indoor"
elif 35 in i2c_devices: # 35 = ltr-599 on grow & weather
  pump3_pin = Pin(12, Pin.IN, Pin.PULL_UP)
  model = "grow" if pump3_pin.value() == False else "weather"
  pump3_pin.init(pull=None)
else:
  model = "urban" # otherwise it's urban..

# return the module that implements this board type
def get_board():
  if model == "indoor":
    import enviro.boards.indoor as board
  if model == "grow":
    import enviro.boards.grow as board
  if model == "weather":
    import enviro.boards.weather as board
  if model == "urban":
    import enviro.boards.urban as board
  return board

# set up the activity led
# ===========================================================================
from machine import PWM, Timer
import math
activity_led_pwm = PWM(Pin(ACTIVITY_LED_PIN))
activity_led_pwm.freq(1000)
activity_led_pwm.duty_u16(0)

# set the brightness of the activity led
def activity_led(brightness):
  brightness = max(0, min(100, brightness)) # clamp to range
  # gamma correct the brightness (gamma 2.8)
  value = int(pow(brightness / 100.0, 2.8) * 65535.0 + 0.5)
  activity_led_pwm.duty_u16(value)

activity_led_timer = Timer(-1)
activity_led_pulse_speed_hz = 1
def activity_led_callback(t):
  # updates the activity led brightness based on a sinusoid seeded by the current time
  brightness = (math.sin(time.ticks_ms() * math.pi * 2 / (1000 / activity_led_pulse_speed_hz)) * 40) + 60
  value = int(pow(brightness / 100.0, 2.8) * 65535.0 + 0.5)
  activity_led_pwm.duty_u16(value)

# set the activity led into pulsing mode
def pulse_activity_led(speed_hz = 1):
  global activity_led_timer, activity_led_pulse_speed_hz
  activity_led_pulse_speed_hz = speed_hz
  activity_led_timer.deinit()
  activity_led_timer.init(period=50, mode=Timer.PERIODIC, callback=activity_led_callback)

# turn off the activity led and disable any pulsing animation that's running
def stop_activity_led():
  global activity_led_timer
  activity_led_timer.deinit()
  activity_led_pwm.duty_u16(0)

# check whether device needs provisioning
# ===========================================================================
import time
from phew import logging
button_pin = Pin(BUTTON_PIN, Pin.IN, Pin.PULL_DOWN)
needs_provisioning = False
start = time.time()
while button_pin.value(): # button held for 3 seconds go into provisioning
  if time.time() - start > 3:
    needs_provisioning = True
    break

try:
  import config # fails to import (missing/corrupt) go into provisioning
  if not config.provisioned: # provisioned flag not set go into provisioning
    needs_provisioning = True
except Exception as e:
  logging.error("> missing or corrupt config.py", e)
  needs_provisioning = True

if needs_provisioning:
  logging.info("> entering provisioning mode")
  import enviro.provisioning
  # control never returns to here, provisioning takes over completely

# all the other imports, so many shiny modules
import machine, sys, os, ujson
from enviro.custom_helpers import initialize_rtc, check_cached_file_is_not_empty, \
  move_incompatible_file_out_of_uploads_dir, is_custom_config_active
import phew
from pcf85063a import PCF85063A
import enviro.config_defaults as config_defaults
import enviro.helpers as helpers

config_defaults.add_missing_config_settings()

# read the state of vbus to know if we were woken up by USB
vbus_present = Pin("WL_GPIO2", Pin.IN).value()

# set up the button, external trigger, and rtc alarm pins
# (I believe this is a misnomer; the datasheet only lists an interrupt pin.)
rtc_alarm_pin = Pin(RTC_ALARM_PIN, Pin.IN, Pin.PULL_DOWN)
# BUG This should only be set up for Enviro Camera
# external_trigger_pin = Pin(EXTERNAL_INTERRUPT_PIN, Pin.IN, Pin.PULL_DOWN)

# intialise the pcf85063a real time clock chip
rtc = initialize_rtc(i2c)

# jazz up that console! toot toot!
print("       ___            ___            ___          ___          ___            ___       ")
print("      /  /\          /__/\          /__/\        /  /\        /  /\          /  /\      ")
print("     /  /:/_         \  \:\         \  \:\      /  /:/       /  /::\        /  /::\     ")
print("    /  /:/ /\         \  \:\         \  \:\    /  /:/       /  /:/\:\      /  /:/\:\    ")
print("   /  /:/ /:/_    _____\__\:\    ___  \  \:\  /__/::\      /  /:/~/:/     /  /:/  \:\   ")
print("  /__/:/ /:/ /\  /__/::::::::\  /___\  \__\:\ \__\/\:\__  /__/:/ /:/___  /__/:/ \__\:\  ")
print("  \  \:\/:/ /:/  \  \:\~~~__\/  \  \:\ |  |:|    \  \:\/\ \  \:\/:::::/  \  \:\ /  /:/  ")
print("   \  \::/ /:/    \  \:\         \  \:\|  |:|     \__\::/  \  \::/~~~`    \  \:\  /:/   ")
print("    \  \:\/:/      \  \:\         \  \:\__|:|     /  /:/    \  \:\         \  \:\/:/    ")
print("     \  \::/        \  \:\         \  \::::/     /__/:/      \  \:\         \  \::/     ")
print("      \__\/          \__\/          `~~~~~`      \__\/        \__\/          \__\/      ")
print("")
print("    -  --  ---- -----=--==--===  hey enviro, let's go!  ===--==--=----- ----  --  -     ")
print("")

disconnect_wifi = None

def reconnect_wifi(ssid, password, country, hostname=None):
  global disconnect_wifi
  import time
  import network
  import math
  import rp2
  import ubinascii

  start_ms = time.ticks_ms()

  # Set country (rp2 does also set the network.country()).
  rp2.country(country)

  # Set hostname.
  if hostname is None:
      hostname = f"EnviroW-{helpers.uid()[-4:]}"
  network.hostname(hostname)
  logging.info("> Hostname: " + hostname)

  # Wake the adapter.
  wlan = network.WLAN(network.STA_IF)
  wlan.active(True)

  # Use performance mode on USB, powersave on battery.
  # These constants are new in Micropython v1.22, which the official enviro
  # 0.2.0 image updated to.
  # https://docs.micropython.org/en/v1.22.0/library/network.WLAN.html
  if vbus_present:
    # This should be a no-op, since it's the default.
    logging.info("  - on USB power, setting performance power profile")
    wlan.config(pm=wlan.PM_PERFORMANCE)
  else:
    logging.info("  - setting power-saving profile")
    wlan.config(pm=wlan.PM_POWERSAVE)

  # Print MAC address.
  mac = ubinascii.hexlify(wlan.config('mac'),':').decode()
  logging.info("> MAC: " + mac)

  status_names = {
    network.STAT_IDLE: "No connection and no activity",
    network.STAT_CONNECTING: "Connecting in progress",
    # This is CYW43_LINK_NOIP, and seems to leak through.
    2: "Waiting for IP address",
    network.STAT_WRONG_PASSWORD: "Failed due to incorrect password",
    network.STAT_NO_AP_FOUND: "Failed because no access point replied",
    network.STAT_CONNECT_FAIL: "Failed due to other problems",
    network.STAT_GOT_IP: "Connection successful",
  }

  fail_statuses = [
    network.STAT_WRONG_PASSWORD,
    network.STAT_NO_AP_FOUND,
    network.STAT_CONNECT_FAIL
  ]

  def dump_status():
    # So, the CYW43 does not seem to follow the Micropython docs for this.
    # While we try, active(bool) doesn't change its state, and active() seems
    # to return if it's *attempting to be connected*.
    # So read status regardless and log if it thinks it's active, rather than
    # assume inactive means those are invalid and it must be idle/disconnected.
    status = wlan.status()
    connected = wlan.isconnected()
    active = wlan.active()
    logging.info(
      f"  - status: {status} ({status_names.get(status, "Unknown")})" +
      (" [connected]" if connected else "") +
      ("" if active else " [INACTIVE]"))
    return (status, connected)

  # Wait for connection/disconnection, throw on timeout or failure.
  def wait_connection(want_connected, timeout):
    for _ in range(timeout):
      time.sleep(1.0)
      (status, connected) = dump_status()
      if want_connected and connected:
        return
      # Wanting to disconnect means going all the way back down to idle, not
      # just "not connected".
      if not want_connected and status == network.STAT_IDLE:
        return
      if status in fail_statuses:
        raise Exception(status_names[status])
    raise Exception("timeout")

  # Set up disconnect handler.
  def disconnect(deactivate=True):
    (status, _) = dump_status()
    if status != network.STAT_IDLE:
      wlan.disconnect()
      try:
        wait_connection(False, 5)
      except Exception as x:
        raise Exception(f"Failed to disconnect: {x}")
      logging.info("  - disconnected successfully")
      if deactivate:
        wlan.active(False)
        logging.info("  - wifi deactivated")
  disconnect_wifi = disconnect

  # Stop meddling if we're already connected.
  # Disconnect if already partially connected for a clean retry.
  (status, connected) = dump_status()
  if connected:
    logging.info("> Already connected!")
    return time.ticks_ms() - start_ms
  if status != network.STAT_IDLE:
    logging.info("> Partially connected; disconnect for retry...")
    disconnect(deactivate=False)

  # Big stupid hammer for big stupid wireless problems.
  # This consumes extra time and battery but also seems to act as a "wait for
  # the CYW43 to find its pants" before asking it to connect.
  def force_wireless_scan():
    logging.info(f"> Forcing wireless scan...")
    for _ in range(3):
      scan = wlan.scan()
      if scan:
        logging.info(f"  - found {len(scan)} access points, promising!")
        return
      else:
        logging.warn("  - not seeing any access points yet...")
    logging.warn("!  Gave up scanning, found nothing, connection unlikely!")
  if is_custom_config_active('force_wireless_scan'):
    force_wireless_scan()

  logging.info("> Ready for connection!")

  # Connect to our AP.
  logging.info(f"> Connecting to SSID {ssid}...")
  wlan.connect(ssid, password)
  try:
    # TODO It'd be nice if this timeout were configurable, eh.
    wait_connection(True, 30)
  except Exception as e:
    raise Exception(f"Failed to connect to SSID {ssid}: {e}")
  logging.info("> Connected successfully!")

  # Show info.
  ip, subnet, gateway, dns = wlan.ifconfig()
  logging.info(f"> IP: {ip}, Subnet: {subnet}, Gateway: {gateway}, DNS: {dns}")
  rssi = wlan.status('rssi')
  logging.info(f"> RSSI (signal strength): {rssi}")

  # Check for bad IP. This *shouldn't* happen since, unlike a raw status check,
  # wlan.isconnected() returns False for GOT_IP if the IP is all-zeroes.
  if ip == "0.0.0.0":
    logging.error("  - ...but IP is bad!")
    disconnect(deactivate=True)
    raise Exception(f"Failed to get valid IP from {ssid} (DHCP problem?)")

  elapsed_ms = time.ticks_ms() - start_ms
  logging.info(f"> Elapsed: {elapsed_ms}ms")
  return elapsed_ms

def connect_to_wifi():
  try:
    logging.info(f"> connecting to wifi network '{config.wifi_ssid}'")
    elapsed_ms = reconnect_wifi(config.wifi_ssid, config.wifi_password, config.wifi_country)
    # a slow connection time will drain the battery faster and may
    # indicate a poor quality connection
    seconds_to_connect = elapsed_ms / 1000
    if seconds_to_connect > 5:
      logging.warn("  - took", seconds_to_connect, "seconds to connect to wifi")
    return True
  except Exception as x:
    logging.error(f"! {x}")
    return False

# log the error, blink the warning led, and go back to sleep
def halt(message):
  logging.error(message)
  warn_led(WARN_LED_BLINK)
  sleep(5)

# log the exception, blink the warning led, and go back to sleep
def exception(exc):
  import sys, io
  buf = io.StringIO()
  sys.print_exception(exc, buf)
  logging.exception("! " + buf.getvalue())
  warn_led(WARN_LED_BLINK)
  sleep(5)

# returns True if we've used up 90% of the internal filesystem
def low_disk_space():
  if not phew.remote_mount: # os.statvfs doesn't exist on remote mounts
    return (os.statvfs(".")[3] / os.statvfs(".")[2]) < 0.1
  return False

# returns True if the rtc clock has been set recently
def is_clock_set():
  # is the year on or before 2020?
  if rtc.datetime()[0] <= 2020:
    return False

  if helpers.file_exists("sync_time.txt"):
    now_str = helpers.datetime_string()
    now = helpers.timestamp(now_str)

    time_entries = []
    with open("sync_time.txt", "r") as timefile:
      time_entries = timefile.read().split("\n")

    # read the first line from the time file
    sync = now
    for entry in time_entries:
      if entry:
        sync = helpers.timestamp(entry)
        break

    seconds_since_sync = now - sync
    if seconds_since_sync >= 0:  # there's the rare chance of having a newer sync time than what the RTC reports
      try:
        if seconds_since_sync < (config.resync_frequency * 60 * 60):
          return True

        logging.info(f"  - rtc has not been synched for {config.resync_frequency} hour(s)")
      except AttributeError:
        return True

  return False

# phew's ntp client has no error reporting at all to log or inform retries.
# (It would be better to make it raise, but then all callers need updating.)
# As a side-effect, it sets the Pico's RTC on success by default; this version
# always does.
def native_ntp(ntp_host):
  import machine, time, usocket, struct

  timestamp = None
  query = bytearray(48)
  query[0] = 0x1b
  logging.debug(f"  - ntp resolve {ntp_host}...")
  address = usocket.getaddrinfo(ntp_host, 123)[0][-1]
  socket = usocket.socket(usocket.AF_INET, usocket.SOCK_DGRAM)
  socket.settimeout(10)
  logging.debug(f"  - ntp send to {address}...")
  socket.sendto(query, address)
  logging.debug(f"  - ntp receive...")
  data = socket.recv(48)
  logging.debug(f"  - ntp received!")
  socket.close()
  local_epoch = 2208988800 # selected by Chris - blame him. :-D
  timestamp = struct.unpack("!I", data[40:44])[0] - local_epoch
  timestamp = time.gmtime(timestamp)

  machine.RTC().datetime((
    timestamp[0], timestamp[1], timestamp[2], timestamp[6],
    timestamp[3], timestamp[4], timestamp[5], 0))

  return timestamp

# connect to wifi and attempt to fetch the current time from an ntp server
def sync_clock_from_ntp():
  if not connect_to_wifi():
    return False
  attempt = 0
  timestamp = None
  while timestamp is None and attempt < 5:
    attempt += 1
    logging.info(f"  - attempt {attempt} to fetch time...")
    try:
      timestamp = native_ntp("pool.ntp.org")
    except Exception as e:
      logging.error(f"  - ntp failure: {e}")
  if not timestamp:
    logging.error("  - failed to fetch time from ntp server")
    return False

  rtc.datetime(timestamp) # set the time on the rtc chip

  # read back the RTC time to confirm it was updated successfully
  dt = rtc.datetime()
  # rtc.datetime() misses the required day-of-year field; it won't match, but
  # mktime() won't care since it doesn't contribute to epoch time.
  diff = abs(time.mktime(timestamp) - time.mktime(dt + (0,)))
  if diff > 1:
    logging.error("  - failed to update rtc")
    if helpers.file_exists("sync_time.txt"):
      os.remove("sync_time.txt")
    return False

  logging.info("  - rtc synched")

  # write out the sync time log
  with open("sync_time.txt", "w") as syncfile:
    syncfile.write("{0:04d}-{1:02d}-{2:02d}T{3:02d}:{4:02d}:{5:02d}Z".format(*timestamp))

  return True

# set the state of the warning led (off, on, blinking)
def warn_led(state):
  if state == WARN_LED_OFF:
    rtc.set_clock_output(PCF85063A.CLOCK_OUT_OFF)
  elif state == WARN_LED_ON:
    rtc.set_clock_output(PCF85063A.CLOCK_OUT_1024HZ)
  elif state == WARN_LED_BLINK:
    rtc.set_clock_output(PCF85063A.CLOCK_OUT_1HZ)

# the pcf85063a defaults to 32KHz clock output so need to explicitly turn off
warn_led(WARN_LED_OFF)


# returns the reason the board woke up from deep sleep
def get_wake_reason():
  import wakeup

  wake_reason = None
  if wakeup.get_gpio_state() & (1 << BUTTON_PIN):
    wake_reason = WAKE_REASON_BUTTON_PRESS
  elif wakeup.get_gpio_state() & (1 << RTC_ALARM_PIN):
    wake_reason = WAKE_REASON_RTC_ALARM
  # TODO Temporarily removing this as false reporting on non-camera boards
  #elif not external_trigger_pin.value():
  #  wake_reason = WAKE_REASON_EXTERNAL_TRIGGER
  elif vbus_present:
    wake_reason = WAKE_REASON_USB_POWERED
  return wake_reason

# convert a wake reason into it's name
def wake_reason_name(wake_reason):
  names = {
    None: "unknown",
    WAKE_REASON_PROVISION: "provisioning",
    WAKE_REASON_BUTTON_PRESS: "button",
    WAKE_REASON_RTC_ALARM: "rtc_alarm",
    WAKE_REASON_EXTERNAL_TRIGGER: "external_trigger",
    WAKE_REASON_RAIN_TRIGGER: "rain_sensor",
    WAKE_REASON_USB_POWERED: "usb_powered"
  }
  return names.get(wake_reason)

# get the readings from the on board sensors
def get_sensor_readings():
  seconds_since_last = 0
  now_str = helpers.datetime_string()
  if helpers.file_exists("last_time.txt"):
    now = helpers.timestamp(now_str)

    time_entries = []
    with open("last_time.txt", "r") as timefile:
      time_entries = timefile.read().split("\n")

    # read the first line from the time file
    last = now
    for entry in time_entries:
      if entry:
        last = helpers.timestamp(entry)
        break

    seconds_since_last = now - last
    logging.info(f"  - seconds since last reading: {seconds_since_last}")


  readings = get_board().get_sensor_readings(seconds_since_last, vbus_present)

  # write out the last time log
  with open("last_time.txt", "w") as timefile:
    timefile.write(now_str)

  return readings

# save the provided readings into a todays readings data file
def save_reading(readings):
  # open todays reading file and save readings
  helpers.mkdir_safe("readings")
  readings_filename = f"readings/{helpers.date_string()}.csv"
  new_file = not helpers.file_exists(readings_filename)
  with open(readings_filename, "a") as f:
    if new_file:
      # new readings file so write out column headings first
      f.write("timestamp," + ",".join(readings.keys()) + "\r\n")

    # write sensor data
    row = [helpers.datetime_string()]
    for key in readings.keys():
      row.append(str(readings[key]))
    f.write(",".join(row) + "\r\n")


# save the provided readings into a cache file for future uploading
def cache_upload(readings):
  payload = {
    "nickname": config.nickname,
    "timestamp": helpers.datetime_string(),
    "readings": readings,
    "model": model,
    "uid": helpers.uid()
  }

  uploads_filename = f"uploads/{helpers.datetime_file_string()}.json"
  helpers.mkdir_safe("uploads")
  with open(uploads_filename, "w") as upload_file:
    # THESE Are the non-failing calls (no: 'TypeError: extra positional arguments given')
    # Even when PyCharm / typing system does not warn about missing keyword parameter
    # when separators param is not named!
    # Using stub: Module: 'ujson' on micropython-v1.22.1-rp2-RPI_PICO_W
    # upload_file.write(ujson.dumps(payload, separators=(',', ':')))
    ujson.dump(payload, upload_file, separators=(',', ':'))

# return the number of cached results waiting to be uploaded
def cached_upload_count():
  try:
    return len(os.listdir("uploads"))
  except OSError:
    return 0

# returns True if we have more cached uploads than our config allows
def is_upload_needed():
  return cached_upload_count() >= config.upload_frequency

# upload cached readings to the configured destination
def upload_readings():
  if not connect_to_wifi():
    logging.error(f"  - cannot upload readings, wifi connection failed")
    return False

  destination = config.destination
  try:
    exec(f"import enviro.destinations.{destination}")
    destination_module = sys.modules[f"enviro.destinations.{destination}"]
    destination_module.log_destination()

    for cache_file in os.ilistdir("uploads"):
      try:
        with open(f"uploads/{cache_file[0]}", "r") as upload_file:
          cache_file_contains_data = check_cached_file_is_not_empty(cache_file[0], upload_file)
          if cache_file_contains_data:
            status = destination_module.upload_reading(ujson.load(upload_file))
          else:
            logging.warn(f"  - skipping (deleting) '{cache_file[0]}' as it is empty!")
            try:
              # Be extra cautious and prepared for any not expected situations at all!
              os.remove(f"uploads/{cache_file[0]}")
              continue
            except (FileNotFoundError, PermissionError, IsADirectoryError, OSError):
              continue

          if status == UPLOAD_SUCCESS:
            os.remove(f"uploads/{cache_file[0]}")
            logging.info(f"  - uploaded {cache_file[0]}")
          elif status == UPLOAD_RATE_LIMITED:
            # write out that we want to attempt a reupload
            with open("reattempt_upload.txt", "w") as attemptfile:
              attemptfile.write("")

            logging.info(f"  - cannot upload '{cache_file[0]}' - rate limited")
            sleep(1)
          elif status == UPLOAD_LOST_SYNC:
            # remove the sync time file to trigger a resync on next boot
            if helpers.file_exists("sync_time.txt"):
              os.remove("sync_time.txt")

            # write out that we want to attempt a reupload
            with open("reattempt_upload.txt", "w") as attemptfile:
              attemptfile.write("")

            logging.info(f"  - cannot upload '{cache_file[0]}' - rtc has become out of sync")
            sleep(1)
          elif status == UPLOAD_SKIP_FILE:
            logging.error(f"  ! cannot upload '{cache_file[0]}' to {destination}. Skipping file")
            warn_led(WARN_LED_BLINK)
            continue
          else:
            logging.error(f"  ! failed to upload '{cache_file[0]}' to {destination}")
            return False

      except OSError:
        logging.error(f"  ! failed to open '{cache_file[0]}'")
        return False

      except KeyError:
        logging.error(f"  ! skipping '{cache_file[0]}' as it is missing data. It was likely created by an older version of the enviro firmware")
        move_incompatible_file_out_of_uploads_dir(cache_file[0])

      except ValueError:
        logging.error(f"  ! skipping '{cache_file[0]}' as it is seems malformed. ")
        move_incompatible_file_out_of_uploads_dir(cache_file[0])

  except ImportError:
    logging.error(f"! cannot find destination {destination}")
    return False

  finally:
    url = is_custom_config_active('log_upload_url')
    if url:
      # Attempt log upload via HTTP. Shares auth with HTTP endpoint.
      import urequests
      logging.info(f"> uploading logfile to url: {url}")
      auth = None
      if config.custom_http_username:
        auth = (config.custom_http_username, config.custom_http_password)
      try:
        with open("log.txt", "r") as upload_file:
          result = urequests.post(url, auth=auth, data=upload_file.read())
          result.close()

          if result.status_code < 200 or result.status_code >= 300:
            logging.debug(f"  - upload issue ({result.status_code} {result.reason})")
      except Exception as e:
        logging.error(f"  ! failed to upload log: {e}")

    # Disconnect wifi
    logging.info("> Disconnecting wireless after upload")
    disconnect_wifi()

  return True

def startup():
  import sys

  # write startup info into log file
  logging.info("> performing startup")
  logging.debug(f"  - running Enviro {ENVIRO_VERSION}, {sys.version.split('; ')[1]}")

  # get the reason we were woken up
  reason = get_wake_reason()

  # give each board a chance to perform any startup it needs
  # ===========================================================================
  board = get_board()
  if hasattr(board, "startup"):
    continue_startup = board.startup(reason)
    # put the board back to sleep if the startup doesn't need to continue
    # and the RTC has not triggered since we were awoken
    if not continue_startup and not rtc.read_timer_flag():
      logging.debug("  - wake reason: trigger")
      sleep()

  # log the wake reason
  logging.info("  - wake reason:", wake_reason_name(reason))

  # also immediately turn on the LED to indicate that we're doing something
  logging.debug("  - turn on activity led")
  pulse_activity_led(0.5)

  # see if we were woken to attempt a reupload
  if helpers.file_exists("reattempt_upload.txt"):
    upload_count = cached_upload_count()
    if upload_count == 0:
      os.remove("reattempt_upload.txt")
      return

    logging.info(f"> {upload_count} cache file(s) still to upload")
    if not upload_readings():
      halt("! reading upload failed")

    os.remove("reattempt_upload.txt")

    # if it was the RTC that woke us, go to sleep until our next scheduled reading
    # otherwise continue with taking new readings etc
    # Note, this *may* result in a missed reading
    if reason == WAKE_REASON_RTC_ALARM:
      sleep()

def sleep(time_override=None):
  # For how long?
  minutes = config.reading_frequency
  if time_override is not None:
    minutes = time_override
  if minutes > 255:
    minutes = 255
  if minutes < 1:
    # Less than one is either going to underflow or be zero (timer disable).
    logging.error(f"!  probable bug, tried to sleep for {minutes} minutes")
    minutes = 1

  # Log about it.
  logging.info(f"> going to sleep for {minutes} minute(s)")
  if minutes == 255:
    logging.warn(f"  - limited to 255-minute maximum timer")

  # Set the timer.
  rtc.unset_timer() # Per datasheet, disable timer while setting new duration.
  rtc.clear_timer_flag()
  rtc.set_timer(minutes, rtc.TIMER_TICK_1_OVER_60HZ)
  rtc.enable_timer_interrupt(True)

  # Disconnect the wifi, if it was, else some routers get upset at us vanishing
  # then trying to connect anew a while later.
  if disconnect_wifi is not None:
    logging.info("  - attempting to disconnect wifi first")
    try:
      disconnect_wifi()
    except Exception as e:
      # We *must not* let any wifi nonsense stop us sleeping.
      logging.error(f"  - wifi disconnect error: {e}")

  # disable the vsys hold, causing us to turn off
  logging.info("  - shutting down")
  hold_vsys_en_pin.init(Pin.IN)

  # if we're still awake it means power is coming from the USB port in which
  # case we can't (and don't need to) sleep.
  stop_activity_led()

  # if running via mpremote/pyboard.py with a remote mount then we can't
  # reset the board so just exist
  if phew.remote_mount:
    sys.exit()

  # we'll wait here until the rtc timer triggers and then reset the board
  logging.debug("  - on usb power (so can't shutdown). Halt and wait for alarm or user reset instead")
  board = get_board()
  while not rtc.read_timer_flag():
    if hasattr(board, "check_trigger"):
      board.check_trigger()

    #time.sleep(0.25)

    if button_pin.value(): # allow button to force reset
      break

  logging.debug("  - reset")

  # reset the board
  machine.reset()
