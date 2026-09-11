import json
import zipfile
import pytest
import build_extensions as build


@pytest.mark.parametrize('browser', build.TARGETS)
def test_packages_shared_code_and_no_user_data(tmp_path, browser):
    folder, archive = build.build(browser, tmp_path/browser)
    manifest = json.loads((folder/'manifest.json').read_text())
    assert manifest['manifest_version'] == 3
    assert (folder/'content.js').read_bytes() == (build.ROOT/'chrome-extension/content.js').read_bytes()
    with zipfile.ZipFile(archive) as z:
        assert 'manifest.json' in z.namelist()
        assert all(not name.startswith(('data/', 'work/', '.env')) for name in z.namelist())
    if browser == 'chrome':
        assert 'key' in manifest
        assert manifest['background']['service_worker'] == 'background.js'
    else:
        assert 'key' not in manifest
        assert manifest['background']['scripts'] == ['archive_config.js', 'background.js']
    if browser == 'firefox':
        assert manifest['browser_specific_settings']['gecko']['id'] == build.FIREFOX_ID
        assert 'authenticationInfo' in manifest['browser_specific_settings']['gecko']['data_collection_permissions']['required']
    if browser == 'safari':
        assert build.SAFARI_APP_ID in (folder/'archive_config.js').read_text()


def test_refuses_to_replace_existing_package(tmp_path):
    folder=tmp_path/'firefox';folder.mkdir();marker=folder/'keep.txt';marker.write_text('keep')
    with pytest.raises(ValueError):build.build('firefox', folder)
    assert marker.read_text() == 'keep'
