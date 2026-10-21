import datetime

from service.routers.userdata import load_userdata, save_userdata


def test_save_and_load(tmp_path):
    assert load_userdata(tmp_path) == (None, None)

    day1 = datetime.datetime(2026, 10, 21, 20, 0, 0)
    save_userdata(tmp_path, {"cijene-users": [{"name": "Frane"}]}, day1)
    save_userdata(tmp_path, {"cijene-users": [{"name": "Frane"}, {"name": "M"}]}, day1)

    data, saved_at = load_userdata(tmp_path)
    assert len(data["cijene-users"]) == 2
    assert saved_at == "2026-10-21T20:00:00"
    assert (tmp_path / "userdata.prev.json").exists()
    assert (tmp_path / "userdata-2026-10-21.json").exists()


def test_old_daily_copies_are_removed(tmp_path):
    (tmp_path / "userdata-2026-01-01.json").write_text("{}")
    save_userdata(tmp_path, {}, datetime.datetime(2026, 10, 21))
    assert not (tmp_path / "userdata-2026-01-01.json").exists()
    assert (tmp_path / "userdata-2026-10-21.json").exists()
