"""Picker subprocess contracts; no native dialogs or user folders are accessed."""
import base64
import subprocess
from unittest.mock import Mock

import pytest
import export_service
from export_service import ExportManager


@pytest.fixture
def chooser(tmp_path,monkeypatch):
    manager=ExportManager(tmp_path/'data')
    run=Mock(return_value=subprocess.CompletedProcess([],0,b'',b''))
    monkeypatch.setattr(export_service.subprocess,'run',run)
    return manager,run


def platform_tools(monkeypatch,system,available):
    monkeypatch.setattr(export_service.platform,'system',lambda:system)
    monkeypatch.setattr(export_service.shutil,'which',lambda name:available.get(name))


def test_macos_returns_unicode_posix_path_and_static_script(chooser,monkeypatch):
    manager,run=chooser
    platform_tools(monkeypatch,'Darwin',{'osascript':'/usr/bin/osascript','powershell.exe':'wrong'})
    selected='/Users/demo/Résultats de l’archive 🐦/'
    run.return_value=subprocess.CompletedProcess([],0,(selected+'\n').encode(),b'')
    assert manager.choose()=={'cancelled':False,'destination':selected}
    command=run.call_args.args[0]
    assert command[:2]==['/usr/bin/osascript','-e']
    assert 'POSIX path of (choose folder' in command[2]
    assert 'on error number -128' in command[2]
    assert selected not in command[2]
    assert run.call_args.kwargs=={'capture_output':True,'timeout':180}
    assert not manager.chooser_lock.locked()


def test_macos_cancel_is_not_an_error(chooser,monkeypatch):
    manager,run=chooser
    platform_tools(monkeypatch,'Darwin',{'osascript':'osascript'})
    run.return_value=subprocess.CompletedProcess([],0,b'\n',b'')
    assert manager.choose()=={'cancelled':True,'destination':''}


def test_macos_script_failure_is_not_cancellation(chooser,monkeypatch):
    manager,run=chooser
    platform_tools(monkeypatch,'Darwin',{'osascript':'osascript'})
    run.return_value=subprocess.CompletedProcess([],1,b'',b'Permission denied')
    with pytest.raises(ValueError,match='indisponible'):manager.choose()
    assert not manager.chooser_lock.locked()


@pytest.mark.parametrize('system,program',[('Windows','powershell.exe'),('Windows','powershell'),('Linux','powershell.exe')])
def test_windows_picker_and_wsl_preserve_encoded_script(chooser,monkeypatch,system,program):
    manager,run=chooser
    platform_tools(monkeypatch,system,{program:'C:/Windows/'+program})
    path='D:\\Résultats X\\Médias'
    run.return_value=subprocess.CompletedProcess([],0,path.encode('utf-8-sig'),b'')
    assert manager.choose()=={'cancelled':False,'destination':path}
    command=run.call_args.args[0]
    assert command[:4]==['C:/Windows/'+program,'-NoProfile','-STA','-EncodedCommand']
    script=base64.b64decode(command[4]).decode('utf-16le')
    assert 'System.Windows.Forms.FolderBrowserDialog' in script
    assert path not in script


@pytest.mark.parametrize('returncode,stdout,expected',[
    (0,b'/home/demo/Results \n',{'cancelled':False,'destination':'/home/demo/Results '}),
    (1,b'',{'cancelled':True,'destination':''}),
])
def test_optional_linux_zenity(chooser,monkeypatch,returncode,stdout,expected):
    manager,run=chooser
    platform_tools(monkeypatch,'Linux',{'zenity':'/usr/bin/zenity'})
    run.return_value=subprocess.CompletedProcess([],returncode,stdout,b'')
    assert manager.choose()==expected
    assert run.call_args.args[0][:3]==['/usr/bin/zenity','--file-selection','--directory']


def test_zenity_failure_is_not_cancellation(chooser,monkeypatch):
    manager,run=chooser
    platform_tools(monkeypatch,'Linux',{'zenity':'zenity'})
    run.return_value=subprocess.CompletedProcess([],255,b'',b'No display')
    with pytest.raises(ValueError,match='indisponible'):manager.choose()


@pytest.mark.parametrize('system',['Darwin','Windows','Linux'])
def test_missing_tool_offers_manual_entry_and_releases_lock(chooser,monkeypatch,system):
    manager,run=chooser
    platform_tools(monkeypatch,system,{})
    with pytest.raises(ValueError,match='saisissez le chemin'):manager.choose()
    run.assert_not_called()
    assert not manager.chooser_lock.locked()


@pytest.mark.parametrize('error,match',[
    (subprocess.TimeoutExpired('osascript',180),'expirée'),
    (OSError('tool removed'),'indisponible'),
])
def test_failed_launch_or_timeout_releases_lock(chooser,monkeypatch,error,match):
    manager,run=chooser
    platform_tools(monkeypatch,'Darwin',{'osascript':'osascript'})
    run.side_effect=error
    with pytest.raises(ValueError,match=match):manager.choose()
    assert not manager.chooser_lock.locked()


def test_duplicate_picker_keeps_original_lock(chooser,monkeypatch):
    manager,run=chooser
    platform_tools(monkeypatch,'Darwin',{'osascript':'osascript'})
    manager.chooser_lock.acquire()
    try:
        with pytest.raises(ValueError,match='déjà ouverte'):manager.choose()
        run.assert_not_called()
        assert manager.chooser_lock.locked()
    finally:manager.chooser_lock.release()
