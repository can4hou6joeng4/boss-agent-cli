import json
from unittest.mock import patch

from boss_agent_cli.auth.token_store import TokenStore


def test_save_and_load(tmp_path):
	store = TokenStore(tmp_path)
	token_data = {
		"cookies": {"wt2": "abc123"},
		"stoken": "zp_stoken_value",
	}
	store.save(token_data)
	loaded = store.load()
	assert loaded == token_data


def test_load_empty(tmp_path):
	store = TokenStore(tmp_path)
	assert store.load() is None


def test_overwrite(tmp_path):
	store = TokenStore(tmp_path)
	store.save({"cookies": {"wt2": "old"}})
	store.save({"cookies": {"wt2": "new"}})
	loaded = store.load()
	assert loaded["cookies"]["wt2"] == "new"


def test_load_invalid_token_returns_none(tmp_path):
	store = TokenStore(tmp_path)
	(store._auth_dir / "session.enc").write_bytes(b"not-a-valid-fernet-token")
	assert store.load() is None


def test_file_lock(tmp_path):
	store = TokenStore(tmp_path)
	with store.refresh_lock():
		assert (tmp_path / "refresh.lock").exists()
	assert not (tmp_path / "refresh.lock").exists()


@patch.dict("os.environ", {"BOSS_AGENT_MACHINE_ID": "test-machine-id"})
def test_machine_id_env_override(tmp_path):
	store = TokenStore(tmp_path)
	assert store._get_machine_id() == "test-machine-id"


@patch("platform.system", return_value="Darwin")
@patch("shutil.which", return_value=None)
def test_machine_id_darwin_without_ioreg_falls_back(mock_which, mock_system, tmp_path):
	store = TokenStore(tmp_path)
	machine_id = store._get_machine_id()
	assert machine_id
	assert machine_id != "boss-agent-cli-fallback-id"
