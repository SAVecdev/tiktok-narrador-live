# Build on the target OS: pyinstaller --noconfirm Narrador.spec
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata
import sys

hidden = collect_submodules('keyring.backends')
datas = [('README.md', '.'), ('LICENSE', '.'), ('THIRD_PARTY_NOTICES.md', '.')]
datas += [('narrador/voice-models.json', 'narrador')]
for package in ['TikTokLive', 'keyring', 'httpx', 'platformdirs', 'PySide6',
                'kokoro-onnx', 'espeakng-loader', 'phonemizer-fork', 'faster-whisper']:
    datas += copy_metadata(package, recursive=True)
voice_binaries = []
for package in ['kokoro_onnx', 'espeakng_loader', 'phonemizer', 'faster_whisper', 'ctranslate2']:
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    voice_binaries += package_binaries
    hidden += package_hidden
a = Analysis(['run.py'], pathex=['.'], binaries=voice_binaries, datas=datas,
             hiddenimports=hidden, hookspath=[], runtime_hooks=[],
             excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
                       'PySide6.QtWebEngineQuick', 'PySide6.QtPdf', 'pytest'],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='TikTokNarradorLive',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='TikTokNarradorLive')
if sys.platform == 'darwin':
    app = BUNDLE(coll, name='TikTokNarradorLive.app', icon=None,
                 bundle_identifier='dev.savec.tiktoknarrador',
                 info_plist={'NSHighResolutionCapable': True,
                             'NSMicrophoneUsageDescription': 'TikTok Narrador transcribe tu dictado localmente para leerlo con la voz seleccionada.'})
