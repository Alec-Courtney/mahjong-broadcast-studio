from pathlib import Path

from PyInstaller.utils.hooks.qt import add_qt6_dependencies

# The scoring engine uses QJSEngine. The desk uses Qt Widgets, so it does not
# need QML imports, Qt Quick modules, or QML debugging plugins.
hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
binaries = [(source, dest) for source, dest in binaries if Path(dest).name != "qmltooling"]
