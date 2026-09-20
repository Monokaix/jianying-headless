#!/usr/bin/env python3
"""Rebuild the source-pinned local codec; never download or patch Jianying."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
BRIDGE = ROOT / 'bridge'
APP = Path('/Applications/VideoFusion-macOS.app')
EXPECTED_CODEC_SHA = 'b6533eb5eb1eea58dfa74fb1d16d3bb580970fe881f587605d358af1745f971d'
PROFILES = {
    '11.4.0': {'short_version': '11.4.0', 'build': '11.4.0',
               'sha256': 'a1693070036a6678bb5db35f71d2105812ad24a2370e7e91c78712cc0d6455f3'},
    '11.4.2': {'short_version': '11.4.2', 'build': '11.4.2',
               'sha256': '632c8ddd09ff4a54f876cd8142eb505055ee26d944199506b230949b7e106bd1'},
    # App Store build of 11.4.0 (build 481), confirmed via codesign on 2026-09-18; distinct profile from '11.4.0' since its CFBundleVersion differs.
    '11.4.0-appstore-481': {'short_version': '11.4.0', 'build': '481',
                             'sha256': 'aea79715de6097394c2f38153e11565f02a823678801cd1eafe90bcccb20c086'},
}


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), 'Expected a regular, non-symlink file: ' + str(path))
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def run(command, *, env=None, timeout=180):
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
    require(result.returncode == 0, 'Command failed: ' + command[0] + '\n' + result.stderr[-4000:])
    return result


def main():
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64',
            'This native bridge profile requires Apple Silicon macOS')
    require(sys.version_info >= (3, 9), 'Python 3.9 or later is required')
    manifest = json.loads((BRIDGE / 'SOURCE_MANIFEST.json').read_text(encoding='utf-8'))
    require(manifest['schema'] == 'jianying-headless-bridge-source/v1', 'Unexpected bridge source manifest')
    for name, expected in manifest['source_files'].items():
        require(Path(name).name == name, 'Bridge source names must be simple file names')
        require(digest(BRIDGE / name) == expected, 'Bridge source changed: ' + name)
    require(manifest['expected_codec_sha256'] == EXPECTED_CODEC_SHA, 'Codec fingerprint changed')

    info = plistlib.loads((APP / 'Contents/Info.plist').read_bytes())
    version = info.get('CFBundleShortVersionString')
    build = info.get('CFBundleVersion')
    profile = next((candidate for candidate in PROFILES.values()
                     if candidate['short_version'] == version and candidate['build'] == build), None)
    require(profile is not None and info.get('CFBundleIdentifier') == 'com.lemon.lvpro',
            'Unsupported Jianying version or identity')
    library = APP / 'Contents/Frameworks/libvideoeditor.dylib'
    require(digest(library) == profile['sha256'], 'The installed editor library does not match this profile')
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('DYLD_') and key not in {'PYTHONHOME', 'PYTHONPATH'}}
    env.update(PATH='/usr/bin:/bin:/usr/sbin:/sbin', LC_ALL='C')
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(APP)], env=env)
    signature = run(['/usr/bin/codesign', '-dv', '--verbose=4', str(APP)], env=env)
    require('TeamIdentifier=X2JNK7LY8J' in signature.stderr.splitlines(), 'Unexpected Jianying signing identity')

    destination = BRIDGE / 'jy14_codec_hardened_11_4'
    if destination.exists() or destination.is_symlink():
        require(digest(destination) == EXPECTED_CODEC_SHA and os.access(destination, os.X_OK),
                'An unexpected codec file already exists; it was not overwritten')
        print(json.dumps({'status': 'already-valid', 'codec_sha256': EXPECTED_CODEC_SHA,
                          'app_version': version, 'network_called': False}))
        return

    work = ROOT / 'work'
    work.mkdir(mode=0o700, exist_ok=True)
    job = Path(tempfile.mkdtemp(prefix='codec-build-', dir=work))
    compiler_temp = job / 'compiler-tmp'
    compiler_temp.mkdir(mode=0o700)
    env.update(TMPDIR=str(compiler_temp) + '/', CLANG_MODULE_CACHE_PATH=str(compiler_temp / 'modules'))
    built = job / destination.name
    frameworks = APP / 'Contents/Frameworks'
    command = ['/usr/bin/xcrun', 'clang++', '-std=c++17', '-arch', 'arm64', '-O2',
               str(BRIDGE / 'jy14_codec.cpp'), '-L' + str(frameworks), '-lvideoeditor',
               '-Wl,-rpath,' + str(frameworks), '-o', str(built)]
    compiler = run(['/usr/bin/xcrun', 'clang++', '--version'], env=env).stdout
    result = run(command, env=env)
    actual = digest(built)
    report = {'schema': 'jianying-headless-codec-build/v1', 'status': 'built', 'app_version': version,
              'compiler': compiler, 'command': command, 'codec_sha256': actual,
              'expected_codec_sha256': EXPECTED_CODEC_SHA, 'stderr': result.stderr,
              'network_called': False, 'official_library_copied': False, 'app_modified': False}
    with (job / 'build-report.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    require(actual == EXPECTED_CODEC_SHA,
            'Compiler output differs from the reviewed codec. No runtime pin was changed; inspect ' + str(job))
    require(digest(library) == profile['sha256'], 'The native library changed while compiling')
    for name, expected in manifest['source_files'].items():
        require(digest(BRIDGE / name) == expected, 'Bridge source changed while compiling: ' + name)
    run(['/usr/bin/codesign', '--verify', '--strict', str(built)], env=env)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o700)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(built.read_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    require(digest(destination) == EXPECTED_CODEC_SHA, 'Installed local codec fingerprint differs')
    print(json.dumps({'status': 'built-and-verified', 'codec_sha256': actual, 'app_version': version,
                      'audit_directory': str(job), 'network_called': False, 'app_modified': False}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error))
