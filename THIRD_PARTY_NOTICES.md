# 第三方组件

Windows 下载包包含 Python、PySide6 / Qt、Shiboken6、Node.js 和 PyInstaller 引导程序。各组件及其包含的第三方库保留原有版权与许可证，完整文本随下载包放在 `licenses/`。Codex CLI 由使用者另行安装，不随本项目分发。

- Python：[PSF 及包含的第三方许可证](https://github.com/python/cpython/blob/main/LICENSE)。
- PySide6、Shiboken6 和 Qt：Community Edition，适用 LGPLv3 / GPLv3 等上游许可；本程序使用 Qt Core、Gui、Widgets、Network 及 SVG 图标插件。对应版本为 6.11.0。[源码与构建说明](https://doc.qt.io/qtforpython-6/building_from_source/index.html)、[Qt 源码归档](https://download.qt.io/archive/qt/6.11/6.11.0/)。
- Node.js：MIT 及其包含的第三方许可证，版本与构建时使用的 Node.js 一致。[源码和许可证](https://github.com/nodejs/node)。
- PyInstaller：GPL 及允许分发打包程序的引导程序例外。[许可说明](https://pyinstaller.org/en/stable/license.html)。

Qt / PySide6 库以动态文件随包提供，可在 `_internal/` 中替换兼容版本；本项目不限制为调试这些 LGPL 库的修改而进行必要的逆向工程。也可从公开源码按 README 的步骤重新构建。
