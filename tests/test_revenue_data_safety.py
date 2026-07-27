import os
from datetime import datetime

import jobs


def test_backup_rotation_keeps_newest_files_by_mtime(tmp_path, monkeypatch):
    data_path = tmp_path / "bot_data.json"
    data_path.write_text('{"orders": {}}', encoding="utf-8")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    now = datetime.now().timestamp()
    for index in range(20):
        old = backup_dir / f"bot_data_before_update_{index:02d}.json"
        old.write_text("{}", encoding="utf-8")
        old_mtime = now - (10_000 + index)
        os.utime(old, (old_mtime, old_mtime))

    monkeypatch.setattr(jobs, "DB_PATH", str(data_path))
    monkeypatch.setattr(jobs, "DATA_DIR", str(tmp_path))

    jobs._backup_database()

    backups = list(backup_dir.glob("bot_data_*.json"))
    startup_backups = [
        path for path in backups if not path.name.startswith("bot_data_before_update_")
    ]
    assert len(backups) == 20
    assert len(startup_backups) == 1
    assert startup_backups[0].stat().st_mtime == max(
        path.stat().st_mtime for path in backups
    )
