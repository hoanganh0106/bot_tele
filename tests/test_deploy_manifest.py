from pathlib import Path


def test_update_script_copies_hypervin_client():
    update_script = Path(__file__).resolve().parents[1] / "update.sh"

    assert '"hypervin_client.py"' in update_script.read_text(encoding="utf-8")
