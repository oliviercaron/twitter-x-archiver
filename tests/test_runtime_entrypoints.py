"""Command-line tools must follow the same archive location as the dashboard."""
import json
import sys

import pytest

from twitter_x_archiver import collector, run_pipeline


@pytest.mark.parametrize('explicit_config', [False, True])
def test_collector_uses_saved_archive_unless_config_is_explicit(tmp_path, monkeypatch, explicit_config):
    config = {'_root': tmp_path, 'data_dir': 'configured-archive'}
    chosen = tmp_path / 'chosen-archive'
    captured = []

    class OfflineCollector:
        def __init__(self, settings):
            captured.append(settings['data_dir'])

        async def run(self, args):
            return 0

    monkeypatch.setattr(collector, 'Collector', OfflineCollector)
    monkeypatch.setattr(collector, 'config_load', lambda _: config)
    monkeypatch.setattr(collector.app_paths, 'ensure_config', lambda: tmp_path / 'config.yaml')
    monkeypatch.setattr(collector.app_paths, 'resolve_data_dir', lambda _: chosen)
    monkeypatch.setattr(sys, 'argv', ['collector', '--mode', 'prepare'] +
                        (['--config', 'custom.yaml'] if explicit_config else []))
    assert collector.main() == 0
    expected = tmp_path / 'configured-archive' if explicit_config else chosen
    assert captured == [str(expected)]
    assert expected.is_dir()


def test_pipeline_records_progress_in_the_selected_archive(tmp_path, monkeypatch):
    chosen = tmp_path / 'chosen-archive'
    commands = []

    class Child:
        pid = 123

        def wait(self):
            return 0

    def spawn(command, **kwargs):
        commands.append(command)
        return Child()

    monkeypatch.setattr(run_pipeline, 'config_load', lambda _: {'queries': []})
    monkeypatch.setattr(run_pipeline.app_paths, 'ensure_config', lambda: tmp_path / 'config.yaml')
    monkeypatch.setattr(run_pipeline.app_paths, 'resolve_data_dir', lambda _: chosen)
    monkeypatch.setattr(run_pipeline.subprocess, 'Popen', spawn)
    monkeypatch.setattr(sys, 'argv', ['pipeline'])
    assert run_pipeline.main() == 0
    assert json.loads((chosen / 'pipeline_status.json').read_text())['status'] == 'finished'
    assert len(commands) == 3
    assert all(command[1:3] == ['-m', 'twitter_x_archiver.collector'] for command in commands)
