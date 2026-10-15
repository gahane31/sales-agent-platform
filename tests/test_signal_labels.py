import re
from pathlib import Path

MIGRATION = Path("pipeline/setup/03_signal_config.sql")
APP = Path("pipeline/app")


def test_signal_wording_is_not_hardcoded_in_the_ui():
    text = MIGRATION.read_text()
    labels = re.findall(r"\('[a-z0-9_]+',\s+'([^']+)'", text)
    assert len(labels) >= 16
    ui_text = "\n".join(path.read_text() for path in APP.rglob("*.py"))
    hardcoded = [label for label in labels if label in ui_text]
    assert hardcoded == []
