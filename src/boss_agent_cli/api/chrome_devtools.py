import os
from pathlib import Path
import re
import sys


def default_chrome_user_data_dir() -> Path:
	if sys.platform == "win32":
		local_app_data = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
		return local_app_data / "Google" / "Chrome" / "User Data"
	if sys.platform == "darwin":
		return Path.home() / "Library/Application Support/Google/Chrome"
	config_home = os.environ.get("CHROME_CONFIG_HOME") or os.environ.get("XDG_CONFIG_HOME")
	return (Path(config_home) if config_home else Path.home() / ".config") / "google-chrome"


def read_chrome_devtools_endpoint(user_data_dir: Path) -> str | None:
	"""Read Chrome's local discovery file without exposing the browser endpoint in errors."""
	try:
		with (user_data_dir / "DevToolsActivePort").open(encoding="ascii") as port_file:
			port = port_file.readline(8).strip()
			path = port_file.readline(128).strip()
	except (OSError, UnicodeError):
		return None
	if not port.isdecimal() or not 1 <= int(port) <= 65535:
		return None
	if re.fullmatch(r"/devtools/browser(?:/[A-Za-z0-9_-]{1,80})?", path) is None:
		return None
	return f"ws://127.0.0.1:{port}{path}"
