#!/usr/bin/env python3
"""Build and run a bounded CPU-only control using the pinned FFmpeg source.

--work must hold ffmpeg-input/, a git checkout of FFmpeg at the pinned revision,
and a receipt.json naming that revision. This diagnostic is not packaged in the
browser. It enables no encoder, network or hardware backend.
"""
import argparse, base64, hashlib, json, os
from pathlib import Path
import subprocess, urllib.request

PIN = '79460ebecaa5625e57a5fb679a735659e73dc687'
FFMPEG = '2b68d2babae73714846961fb0ee47e3b3d2e39a9'
FILES = {
    'test-25fps.hevc': ('hevc', 8, 'test-25fps.hevc.json'),
    'test-25fps.hevc10': ('hevc', 10, 'test-25fps.hevc10.json'),
    'bear-1280x720-hevc-no-audio.mp4': ('mp4', 8, None),
    'bear-1280x720-hevc-10bit-no-audio.mp4': ('mp4', 10, None),
}

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def validate_decode_result(result, depth, expected=None):
    assert result['diagnostic_only'] is True and result['corpus_admission'] is False
    assert result['software_only'] is True and result['decoder'] == 'hevc'
    assert len(result['passes']) == 2 and result['seek_and_flush_completed'] is True
    first, second = result['passes']
    assert first == second, 'seek/reset changed frames'
    assert first['bit_depth'] == depth and first['frames'] > 0
    assert first['frames'] == len(first['frame_md5'])
    assert first['width'] > 0 and first['height'] > 0
    assert all(len(value) == 32 and all(c in '0123456789abcdef' for c in value)
               for value in first['frame_md5'])
    check = {'frames':first['frames'],'bit_depth':depth,'width':first['width'],'height':first['height'],
             'seek_reset_identical':True,'golden_md5_matched':False}
    if expected is not None:
        assert first['frame_md5'] == expected['md5_checksums'], 'native golden mismatch'
        assert first['frames'] == expected['num_frames']
        assert first['width'] == expected['width'] and first['height'] == expected['height']
        assert depth == expected.get('bit_depth',8)
        check['golden_md5_matched'] = True
    return check

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--download-only', action='store_true')
    parser.add_argument('--run-name', default='decode-control')
    args = parser.parse_args()
    work = args.work.resolve()
    assert json.loads((work/'receipt.json').read_text())['ffmpeg_revision'] == FFMPEG
    source = work/'ffmpeg-input'
    assert subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip() == FFMPEG
    fixtures = work/'fixtures'; fixtures.mkdir(exist_ok=True)
    expected_manifest = json.loads((Path(__file__).resolve().parent/'fixtures/hevc/manifest.json').read_text())
    assert expected_manifest['chromium_revision'] == PIN and expected_manifest['ffmpeg_revision'] == FFMPEG
    manifest = {'chromium_revision': PIN, 'ffmpeg_revision': FFMPEG, 'files': {}}
    for name in sorted(set(FILES) | {entry[2] for entry in FILES.values() if entry[2]}):
        url = f'https://chromium.googlesource.com/chromium/src/+/{PIN}/media/test/data/{name}?format=TEXT'
        path = fixtures/name
        if not path.exists():
            path.write_bytes(base64.b64decode(urllib.request.urlopen(url,timeout=60).read()))
        manifest['files'][name] = {'source':url, 'sha256':digest(path), 'bytes':path.stat().st_size}
        assert manifest['files'][name] == expected_manifest['files'][name], 'pinned fixture mismatch: '+name
    manifest_path = fixtures/'manifest.json'
    if manifest_path.exists():
        assert json.loads(manifest_path.read_text()) == manifest, 'fixture bytes changed'
    manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
    if args.download_only:
        print(json.dumps({'downloaded':len(manifest['files']), 'manifest':str(manifest_path)})); return
    assert os.environ.get('APOSTATE_BUILD_IMAGE_ID'), 'requires pinned container'
    assert args.run_name and Path(args.run_name).name == args.run_name
    build = work/args.run_name; build.mkdir(exist_ok=False)
    env = dict(os.environ)
    clang_dir = work/'hevc/src/third_party/llvm-build/Release+Asserts/bin'
    env['PATH'] = str(clang_dir)+':'+str(work/'hevc/tools')+':'+env['PATH']
    os.nice(10)
    configure = [str(source/'configure'), '--cc=clang', '--ld=clang', '--ar=llvm-ar', '--nm=llvm-nm',
        '--ranlib=llvm-ar s', '--disable-everything', '--disable-autodetect', '--disable-network',
        '--disable-programs', '--disable-doc', '--disable-debug', '--disable-shared', '--enable-static',
        '--disable-avdevice', '--disable-avfilter', '--disable-swresample', '--disable-swscale',
        '--disable-encoders', '--disable-hwaccels', '--enable-decoder=hevc', '--enable-parser=hevc',
        '--enable-demuxer=hevc,mov', '--enable-protocol=file', '--enable-pthreads']
    diagnostic_source = Path(__file__).resolve().parent/'fixtures/hevc/decode-check.c'
    commands = [configure, ['make','-j8','libavformat/libavformat.a','libavcodec/libavcodec.a','libavutil/libavutil.a'],
        ['clang','-std=c11','-O2','-I'+str(source),'-I'+str(build),str(diagnostic_source),
         'libavformat/libavformat.a','libavcodec/libavcodec.a','libavutil/libavutil.a','-lm','-lpthread','-o','decode-check']]
    receipt = {'diagnostic_only':True,'corpus_admission':False,'image_id':os.environ['APOSTATE_BUILD_IMAGE_ID'],
        'ffmpeg_revision':FFMPEG,'chromium_revision':PIN,'fixture_manifest':manifest,'commands':commands,
        'source_sha256':digest(diagnostic_source),'checks':{},'passed':False}
    try:
        for i, command in enumerate(commands):
            with (build/f'build-{i}.log').open('w') as log:
                subprocess.run(command,cwd=build,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
        receipt['binary_sha256'] = digest(build/'decode-check')
        receipt['config_sha256'] = digest(build/'config.h')
        for name,(kind,depth,golden) in FILES.items():
            with (build/(name+'.log')).open('w') as log:
                run = subprocess.run([str(build/'decode-check'),str(fixtures/name),kind],cwd=build,
                    env=env,stdout=subprocess.PIPE,stderr=log,check=False,timeout=60)
            (build/(name+'.json')).write_bytes(run.stdout)
            assert run.returncode == 0, name+' decode failed'
            result = json.loads(run.stdout)
            expected = json.loads((fixtures/golden).read_text()) if golden else None
            receipt['checks'][name] = validate_decode_result(result, depth, expected)
        receipt['passed'] = True
    finally:
        (build/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt['checks'],indent=2))
if __name__ == '__main__': main()
