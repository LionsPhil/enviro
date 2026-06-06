import time

from uio import StringIO
from umachine import RTC
from pcf85063a import PCF85063A
from phew import logging

from pimoroni_i2c import PimoroniI2C

from enviro.helpers import mkdir_safe, copy_file, file_size

try:
  import custom_config
except ImportError:
  custom_config = {}

def is_custom_config_active(key: str) -> bool:
  return hasattr(custom_config, key) and getattr(custom_config, key, False)


def check_cached_file_is_not_empty(cache_file_name: str, upload_file: StringIO) -> bool:
  """

  :param cache_file_name: The name of the file in the uploads directory to check, e.g. ``'2023-01-01_12-00-00.json'``.
  :param upload_file: The file object returned by `open` for the cache_file.
  :return: bool: True if the cache file is not empty, False otherwise.
  """
  if file_size(f'uploads/{cache_file_name}'):
    return True
  else:
    current_pos = upload_file.tell()  # Should be 0 anyway but better safe than sorry.
    first_eight_bytes = upload_file.read(8)
    if str == type(first_eight_bytes) and len(first_eight_bytes) > 0: # We seem to have some data in the file. Hopefully it's also a valid json!
      upload_file.seek(current_pos) # Reset the reading position for later effective read for the upload!
      return True
    else:
      return False


def move_incompatible_file_out_of_uploads_dir(cache_file_name: str):
  mkdir_safe('impossible_uploads')
  failing_file = f'uploads/{cache_file_name}'
  copy_file(failing_file, f'impossible_uploads/{cache_file_name}')
