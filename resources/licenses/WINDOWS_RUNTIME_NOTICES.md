# Windows 运行库许可 / Windows runtime notices

## Qt、PySide6 和 Shiboken

Windows 基础版动态链接 Qt，并使用 Qt for Python 的 PySide6 Essentials 和 Shiboken6。Qt 的版权归 The Qt Company Ltd. 及贡献者所有。项目按 GNU LGPL v3 使用这些组件。LGPL v3 的条款包含 GPL v3，程序包随附两份原文。[LGPL v3](LGPL-3.0-only.txt) · [GPL v3](GPL-3.0-only.txt) · [Qt for Python](https://doc.qt.io/qtforpython-6/) · [PySide6 Essentials 6.12.0](https://pypi.org/project/PySide6-Essentials/6.12.0/)

## 对应源码 / Corresponding source

Windows 包直接使用 Qt 和 Qt for Python 官方发布的 6.12.0 运行库。可从下列上游发布档案免费获取对应源码及构建文件。

- [Qt Base 6.12.0](https://download.qt.io/official_releases/qt/6.12/6.12.0/submodules/qtbase-everywhere-src-6.12.0.tar.xz) · [Qt Declarative 6.12.0](https://download.qt.io/official_releases/qt/6.12/6.12.0/submodules/qtdeclarative-everywhere-src-6.12.0.tar.xz) · [Qt SVG 6.12.0](https://download.qt.io/official_releases/qt/6.12/6.12.0/submodules/qtsvg-everywhere-src-6.12.0.tar.xz)
- [Qt Image Formats 6.12.0](https://download.qt.io/official_releases/qt/6.12/6.12.0/submodules/qtimageformats-everywhere-src-6.12.0.tar.xz) · [Qt Translations 6.12.0](https://download.qt.io/official_releases/qt/6.12/6.12.0/submodules/qttranslations-everywhere-src-6.12.0.tar.xz)
- [PySide6 与 Shiboken 6.12.0](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.12.0-src/pyside-setup-everywhere-src-6.12.0.tar.xz) · [Qt for Python 构建说明](https://doc.qt.io/qtforpython-6/building_from_source/index.html)

## 组件与替换 / Components and replacement

基础版使用 Qt Core、Gui、Widgets、Network、Qml 和 Svg。计分引擎使用 Qml 模块提供的 JavaScript 引擎。Qt 图像插件和软件 OpenGL 渲染器也随包提供。`opengl32sw.dll` 为 Mesa llvmpipe，涉及 MIT 和 Boost Software License 1.0。[随包第三方版权文本](QT-THIRD-PARTY-NOTICES.txt) · [Mesa llvmpipe 版权及条款](https://doc.qt.io/qt-6/qt-attribution-llvmpipe.html)

Windows 构建采用目录模式。Qt 和 Shiboken 的动态库及 Qt 插件在可执行文件旁的 `_internal` 目录中。替换或调试运行库时，保留原来的目录结构，并选择与程序兼容的 Python ABI、CPU 架构和 Qt 版本。先在程序包副本中替换相应的 DLL 和插件，再从副本启动主程序。LGPL 允许用户修改库，并为调试这些修改进行逆向工程。[Qt 的 LGPL 发行说明](https://www.qt.io/development/open-source-lgpl-obligations)

发布程序包时，需按实际收录的 Qt 模块、插件和 Python 绑定核对第三方版权及许可，随下载提供适用的完整对应源码或满足许可证的获取方式，并在打包后的副本中验证运行库可替换。使用修改过的运行库时，对应源码需包含相关修改和构建所需的文件。[Qt 第三方许可](https://doc.qt.io/qt-6/licenses-used-in-qt.html) · [Qt for Python 第三方许可](https://doc.qt.io/qtforpython-6/licenses.html)

## CPython 和 PyInstaller

Windows 构建使用构建环境中的 CPython。`PYTHON-LICENSE.txt` 来自该环境的 CPython 安装目录，包含 Python Software Foundation 许可及 Python 附带的版权和许可。随 CPython 提供的 OpenSSL 3.x 由 OpenSSL Project Authors 等贡献者开发，使用 Apache License 2.0。发行时按实际随附的 Visual C++ 运行库文件核对来源及分发条件。[Python 许可历史和完整条款](https://docs.python.org/3/license.html) · [OpenSSL 许可](https://openssl-library.org/source/license/index.html) · [Microsoft Visual C++ 分发条款说明](https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files)

PyInstaller 用于制作 Windows 程序包。其引导程序的分发例外允许按应用自己的条款发布由 PyInstaller 生成的程序包。程序包所含的各组件适用各自的许可证。[PyInstaller 许可证与例外](https://pyinstaller.org/en/stable/license.html)

## English summary

This Windows build dynamically links Qt, PySide6 Essentials, and Shiboken6 6.12.0 under LGPL v3. The license texts and links to the versioned source archives are above. To test a replacement library, make a copy of the program directory. Replace the relevant binaries and Qt plugins under `_internal` with versions compatible with the Python ABI, CPU architecture, and Qt version. Keep the directory layout and launch the executable in the copy. LGPL v3 permits modifying the library and reverse engineering to debug those modifications. [Qt LGPL obligations](https://www.qt.io/development/open-source-lgpl-obligations)

The Windows package includes `PYTHON-LICENSE.txt` from its build environment. Each redistributed component follows its own terms. For each published binary, verify the component and plugin licenses, access to the complete corresponding source, and that the program can run with compatible replacement libraries. [Qt third-party licenses](https://doc.qt.io/qt-6/licenses-used-in-qt.html) · [Python license](https://docs.python.org/3/license.html) · [PyInstaller license and exception](https://pyinstaller.org/en/stable/license.html)
