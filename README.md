# video2pdfslides：视频截图与 PDF 生成工具

将本地演示视频、课程录像中的稳定画面保存为 PNG，并自动合成为 PDF。适合以幻灯片、文档、终端演示为主的视频。

这是一个 Python 命令行工具，没有网页界面。当前默认 **每 2 秒采样 1 次**；采样后还会检测画面变化，只有满足稳定条件的画面才会保存，所以并不是每 2 秒输出一张图片。

## 目录

- [功能概览](#功能概览)
- [环境与安装](#环境与安装)
- [快速开始](#快速开始)
- [命令行参数](#命令行参数)
- [常用命令](#常用命令)
- [采样频率与截图数量](#采样频率与截图数量)
- [裁剪页眉和页脚](#裁剪页眉和页脚)
- [自动检测并移除二维码](#自动检测并移除二维码)
- [处理流程与检测参数](#处理流程与检测参数)
- [输出文件与覆盖规则](#输出文件与覆盖规则)
- [从已有截图重新生成 PDF](#从已有截图重新生成-pdf)
- [查看进度与排查问题](#查看进度与排查问题)
- [测试与项目结构](#测试与项目结构)
- [原项目信息](#原项目信息)

## 功能概览

| 功能 | 说明 |
| --- | --- |
| 按时间间隔采样 | 当前默认间隔 2 秒，可修改脚本中的 `FRAME_RATE` |
| 稳定画面截图 | 使用 OpenCV MOG2 背景模型判断画面变化，在画面稳定后保存 |
| PNG 输出 | 保留原视频宽度；未裁剪时保留原画面尺寸 |
| 自动生成 PDF | 提取结束后自动合成，无需输入 `y` |
| 可选人工检查 | `--review` 暂停流程，允许检查、删除多余截图后再合成 |
| 上下裁剪 | `--crop-top`、`--crop-bottom` 按像素移除页眉、页脚区域 |
| 二维码移除 | `--remove-qr` 检测二维码，并用附近背景色覆盖检测区域 |
| 组合处理 | 二维码移除、上下裁剪、人工检查可以一起使用 |
| Windows 路径 | 支持带盘符、反斜杠、空格的路径，保留 `1.1.mp4` 中的多点文件名 |
| 视频末尾退出 | 在视频帧数和帧率有效时按时长限制采样，避免越界重复读取末帧 |

PDF 由截图组成，不包含 OCR 文字层，也不会提取音频、字幕或恢复原始 PPT 文件。

## 环境与安装

以下命令以 **Windows PowerShell** 为例，从项目根目录执行：

```powershell
Set-Location "F:\Projects\video2pdf"
```

输出路径相对于当前工作目录。建议始终先进入项目目录，再运行脚本。

### 已经配置好的本机环境

本项目当前已验证的环境为 **Windows、Python 3.9.25、NumPy 1.26.4**。项目内已有 `.venv` 时，可以直接使用，无需激活虚拟环境：

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe video2pdfslides.py --help
```

使用项目里的 Python 路径，避免系统 `python` 指向其他版本。依赖中包含较旧版本的 OpenCV，不应假定所有新版 Python 都能直接安装这些依赖。

### 首次创建环境：使用 uv

本机已安装 `uv`。如果在其他机器执行本方案，需要先准备 `uv`。以下环境变量把下载缓存与托管 Python 放在项目目录中：

```powershell
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
$env:UV_PYTHON_INSTALL_DIR = "$PWD\.uv-python"
uv venv --python 3.9 --managed-python .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
```

已有可用 `.venv` 时不要重复创建；更新代码后需要补装依赖，只执行：

```powershell
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
```

通过上述方式创建的虚拟环境会引用 `.uv-python` 中的解释器，使用期间应保留该目录。

### 首次创建环境：使用已安装的 Python 3.9

如果机器上已安装 Python 3.9，也可以使用标准 `venv` 和 `pip`：

```powershell
py -3.9 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

这是另一种安装方式，与 uv 方案选择其一即可。uv 创建的环境不一定自带 `pip`，此时使用前面的 `uv pip install` 命令。

### 依赖说明

实际安装约束以 [requirements.txt](requirements.txt) 为准：

| 依赖 | 版本约束 | 用途 |
| --- | --- | --- |
| `imutils` | `0.5.4` | 缩放用于画面分析的图像 |
| `opencv_python` | `4.5.2.52` | 视频读取、背景建模、图像处理和 PNG 保存 |
| `img2pdf` | `0.4.1` | 将 PNG 合成为 PDF |
| `numpy` | `<2` | 图像数组处理；兼容当前 OpenCV |
| `zxing-cpp` | `2.3.0` | 二维码识别与定位 |

Pillow、pikepdf 等间接依赖会随上述依赖安装。安装需要联网，处理本地视频时不需要在线服务；二维码内容不会被打开或访问。

## 快速开始

### 运行本地样例

以下样例视频当前保存在本机 `input/` 目录。视频文件通过 `.gitignore` 排除，不随 GitHub 仓库上传；首次克隆后，请自行放入视频，或将命令中的路径替换为自己的视频路径。

```powershell
.\.venv\Scripts\python.exe video2pdfslides.py ".\input\Test Video 1.mp4"
```

正常完成后生成：

```text
output/
├── Test Video 1/       # PNG 截图
└── Test Video 1.pdf    # 自动生成的 PDF
```

也可以运行第二个样例：

```powershell
.\.venv\Scripts\python.exe video2pdfslides.py ".\input\Test Video 2.mp4"
```

截图数量取决于当前采样频率和检测参数，不应把旧配置下的样例页数当作固定结果。

### 处理自己的视频

```powershell
.\.venv\Scripts\python.exe video2pdfslides.py "F:\Download\01-chapt1\1.1.mp4"
```

生成 `output\1.1\` 和 `output\1.1.pdf`。路径建议加双引号，尤其是包含空格时。相对路径和正斜杠路径同样可用。

本项目已实际使用 MP4 视频，测试还使用 AVI。其他视频能否读取取决于当前 OpenCV 的解码支持。

## 命令行参数

```text
python video2pdfslides.py <video_path> [--review] [--crop-top PIXELS] [--crop-bottom PIXELS] [--remove-qr]
```

| 参数 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `video_path` | 是 | 无 | 本地视频路径 |
| `--review` | 否 | 关闭 | 截图提取完成后暂停，等待人工确认 |
| `--crop-top PIXELS` | 否 | `0` | 从原画面顶部裁掉的像素行数，非负整数 |
| `--crop-bottom PIXELS` | 否 | `0` | 从原画面底部裁掉的像素行数，非负整数 |
| `--remove-qr` | 否 | 关闭 | 自动识别并覆盖二维码区域 |
| `-h`、`--help` | 否 | 无 | 显示帮助并退出 |

当前没有采样频率或输出目录的命令行参数。它们分别通过脚本中的 `FRAME_RATE`、`OUTPUT_SLIDES_DIR` 修改。

## 常用命令

### 自动生成 PDF

```powershell
.\.venv\Scripts\python.exe video2pdfslides.py "F:\Download\01-chapt1\1.1.mp4"
```

### 去二维码，保留完整画面

```powershell
.\.venv\Scripts\python.exe video2pdfslides.py "F:\Download\01-chapt1\1.1.mp4" --remove-qr
```

### 去二维码，同时裁掉底部 64 像素

适用于希望保留幻灯片标题、移除二维码和页脚区域的当前视频：

```powershell
.\.venv\Scripts\python.exe video2pdfslides.py "F:\Download\01-chapt1\1.1.mp4" --remove-qr --crop-bottom 64
```

### 上裁 100 像素，下裁 64 像素

```powershell
.\.venv\Scripts\python.exe video2pdfslides.py "F:\Download\01-chapt1\1.1.mp4" --crop-top 100 --crop-bottom 64
```

### 提取后先人工检查

```powershell
.\.venv\Scripts\python.exe video2pdfslides.py "F:\Download\01-chapt1\1.1.mp4" --remove-qr --crop-bottom 64 --review
```

看到 `Press y to continue and n to terminate` 后：

1. 打开程序打印出的截图目录，检查 PNG。
2. 删除重复或不需要的图片，至少保留一张。
3. 回到终端输入 `y` 并回车，使用目录中剩余 PNG 生成 PDF。
4. 输入 `n` 并回车则结束本次运行，保留截图，不生成新的 PDF。

输入不区分大小写，其他输入会再次提示。未指定 `--review` 时不会等待此步骤。

## 采样频率与截图数量

编辑 [video2pdfslides.py](video2pdfslides.py) 顶部的 `FRAME_RATE`：

```python
FRAME_RATE = 0.5
```

`FRAME_RATE` 的单位是“每秒采样次数”，不是“每隔多少秒”。换算关系为：

```text
采样间隔（秒） = 1 / FRAME_RATE
```

| `FRAME_RATE` | 采样间隔 |
| --- | --- |
| `3` | 约 0.333 秒 |
| `1.5` | 约 0.667 秒 |
| `1` | 1 秒 |
| `0.5` | 2 秒，当前默认值 |
| `0.25` | 4 秒 |

该值必须大于 0。当前代码没有为手动修改后的非法频率做额外校验。

**采样间隔加倍，最终截图数量不一定减半。** 每个采样点只是候选画面，脚本还需要判断它是否稳定、是否已经保存过当前稳定段。频率变化还会影响背景模型的学习过程。

本机此前对同一段约 37 分 49 秒的视频做过对比：每秒 3 次得到 132 张，每秒 1.5 次得到 110 张，每秒 0.5 次得到 74 张。这些是特定视频和参数下的结果，不是数量换算公式。裁剪或移除二维码也可能改变最终张数。

减少采样次数通常能缩短处理时间，但可能错过短暂出现的页面。如果目标是减少重复页，还需要结合人工检查或画面检测参数处理，不能只依赖增大间隔。

## 裁剪页眉和页脚

`--crop-top` 和 `--crop-bottom` 的单位均为原始视频像素，与视频分辨率有关。

例如，1280×720 视频使用：

```text
--crop-top 100 --crop-bottom 64
```

输出尺寸为：

```text
宽度：1280
高度：720 - 100 - 64 = 556
```

裁剪量必须非负，且两者之和小于画面高度。启用裁剪时，脚本会先读取一帧验证参数，再创建输出目录。非法裁剪参数会报错退出。

裁剪按整行移除内容，不会区分水印、标题、工具栏或正文。当前视频上裁 100 像素会同时裁掉部分页面标题；需要保留标题时，可使用 `--remove-qr` 处理二维码，只对页脚进行必要裁剪。

裁剪后的画面会同时用于变化检测和 PNG 保存，PDF 也使用这些裁剪后的 PNG。默认两个参数均为 `0`，不会裁剪。

## 自动检测并移除二维码

启用 `--remove-qr` 后，程序对每个采样帧执行以下处理：

1. 使用 ZXing 检测二维码，支持同一帧中的多个二维码。
2. 取得二维码多边形位置，向外扩展约 4 像素，覆盖边缘残留。
3. 从周边像素估算背景色，使用每个颜色通道的第 75 百分位数，偏向较亮背景。
4. 用该颜色填充检测区域，再进行可选裁剪和画面变化检测。

未检出二维码的画面保持不变。该功能不改变画面尺寸，二维码区域及其边缘之外的像素保持不变。

适用边界：

- 适合浅色或相对均匀背景的幻灯片；复杂图片、渐变背景上可能出现可见色块。
- 这是局部覆盖，不会还原二维码下面被遮挡的内容。
- 模糊、过小、残缺或无法识别的二维码可能保留，需要检查输出。
- 只处理二维码，不会自动删除文字水印、品牌 Logo、网址或其他条码。
- 二维码可能位于画面任何位置，不限于右上角；程序不会打开其中的网址。

如同时使用裁剪参数，顺序是**先去二维码，再裁剪**，避免二维码先被裁成残片而难以识别。

## 处理流程与检测参数

整体流程：

```text
打开视频
  → 按采样间隔读取帧（默认目标时刻为 0、2、4……秒）
  → 可选：检测并覆盖二维码
  → 可选：裁掉顶部和底部
  → 保留当前尺寸作为待保存画面
  → 另行缩放到宽 600 像素，用于背景模型分析
  → 画面稳定且满足捕获条件时保存 PNG
  → 到达视频结束位置
  → 可选：--review 人工检查
  → 按 PNG 文件名排序，合成 PDF
```

宽 600 像素的缩图只用于分析。输出 PNG 保留原视频宽度及裁剪后的高度，不会因为分析缩放而变成宽 600 像素。

### 脚本常量

以下参数均位于 `video2pdfslides.py` 顶部，需要修改代码后重新运行：

| 常量 | 当前值或表达式 | 作用 |
| --- | --- | --- |
| `OUTPUT_SLIDES_DIR` | `"./output"` | 输出根目录，相对于当前工作目录 |
| `FRAME_RATE` | `0.5` | 每秒采样次数 |
| `WARMUP` | `FRAME_RATE`，当前为 `0.5` | 与已采样帧计数比较的初始等待阈值，不是秒数 |
| `FGBG_HISTORY` | `max(1, round(FRAME_RATE * 15))`，当前为 `8` | 背景模型历史长度，单位为采样帧，至少为 1 |
| `VAR_THRESHOLD` | `16` | 背景模型判断像素是否属于背景的距离阈值 |
| `DETECT_SHADOWS` | `False` | 是否启用阴影检测 |
| `MIN_PERCENT` | `0.1` | 前景变化占比低于 0.1% 时，认为画面足够稳定 |
| `MAX_PERCENT` | `3` | 保存后，变化占比达到 3% 才允许再次等待并捕获下一张 |

画面稳定还需满足“尚未保存当前稳定段”和 `frame_count > WARMUP`。`frame_count` 从 1 开始，只有这个整数计数严格大于阈值后才允许捕获；当前 `WARMUP = 0.5` 不表示等待 0.5 秒。

### 去重方式与调参

当前检测不是全局图片相似度去重。它在一个稳定段中保存一次，发生足够变化后重新等待下一次稳定。因此，回到之前讲过的页面仍可能再次截图。

- **小动画、鼠标操作、滚动导致截图较多：** 可以适当提高 `MAX_PERCENT`，让较小变化不再触发下一张，但可能漏掉局部内容更新。
- **页面一直难以达到稳定条件：** 可以适当提高 `MIN_PERCENT`，但可能保存还在变化的画面。
- **希望更严格地等待稳定：** 可以降低 `MIN_PERCENT`，代价是可能减少截图或漏页。
- **视频动画较多：** 结合采样频率和背景历史长度逐项试验，不要一次修改很多参数。

`MIN_PERCENT`、`MAX_PERCENT` 的单位是百分数。例如 `0.1` 表示 0.1%，不是 10%。

修改 `FRAME_RATE` 会同时改变按它计算的 `WARMUP` 和 `FGBG_HISTORY`。后者必须保持正整数，现有公式已处理。

## 输出文件与覆盖规则

### 目录命名

输入 `F:\Download\01-chapt1\1.1.mp4` 时，视频文件名主体为 `1.1`，不会截断成 `1`。

| 选项 | PNG 目录 | PDF 文件 |
| --- | --- | --- |
| 无处理选项 | `output/1.1/` | `output/1.1.pdf` |
| `--remove-qr` | `output/1.1_noqr/` | `output/1.1_noqr.pdf` |
| `--crop-top 100 --crop-bottom 64` | `output/1.1_crop_t100_b64/` | `output/1.1_crop_t100_b64.pdf` |
| `--crop-bottom 64` | `output/1.1_crop_t0_b64/` | `output/1.1_crop_t0_b64.pdf` |
| `--crop-bottom 64 --remove-qr` | `output/1.1_crop_t0_b64_noqr/` | `output/1.1_crop_t0_b64_noqr.pdf` |

`--review` 不改变输出名称；命令行选项的先后顺序也不改变后缀规则。不同处理选项可以保留不同版本。

### 重跑前必须了解的行为

- 同名截图目录存在时，程序会**删除整个目录并重新创建**，目录中的手动筛选结果和其他文件也会被清除。
- 生成 PDF 时会覆盖同名 PDF。
- 采样频率、检测阈值、输入视频的父目录和扩展名都不包含在输出名称中。因此，不同目录下的同名视频，或者同一视频改变采样频率后重跑，可能覆盖同一组输出。
- 已存在的 PDF 不会在开始提取时自动删除。如果本次中断或在 `--review` 中选择 `n`，旧 PDF 可能仍留在原处。应结合修改时间和完成日志判断是否为本次结果。
- 需要保留比较版本时，先备份现有输出，或修改 `OUTPUT_SLIDES_DIR` 指向其他输出根目录。

此前本机实验中的 `1.1_interval_x2` 是单独保存的对比目录，不是脚本自动根据采样频率生成的命名。

### PNG 文件名和顺序

示例：`003_1.47.png`。

- `003`：截图序号，从 `000` 开始，至少补齐 3 位。
- `1.47`：分钟数的小数形式，四舍五入到两位小数，不是“1 分 47 秒”。
- 当前实现写入文件名的是增加一个采样间隔后的时间值，相对于实际定位时刻晚一个采样间隔，默认约 2 秒。时间字段仅适合粗略定位，不是精确时间码。

PDF 使用截图目录中 `*.png` 的文件名字典序。删除多余截图后无需重新编号，但任意改名可能改变顺序。截图序号超过 999 时，当前至少三位补零的命名不能保证字典序等同于数字顺序，需要额外检查。

## 从已有截图重新生成 PDF

如果截图已经提取完成，只想合成或重新合成 PDF，可以直接调用转换函数，避免重跑视频时清空截图目录。

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -c "from video2pdfslides import convert_screenshots_to_pdf; convert_screenshots_to_pdf(r'output\1.1')"
```

去二维码版本示例：

```powershell
.\.venv\Scripts\python.exe -c "from video2pdfslides import convert_screenshots_to_pdf; convert_screenshots_to_pdf(r'output\1.1_noqr')"
```

这只读取指定目录下的 PNG，生成同路径加 `.pdf` 的文件，不会重新读取视频，也不会再次执行去二维码或裁剪。文件已存在时会覆盖。

调用前确认目录中至少有一张 PNG。空目录无法合成；当前实现会报 `Unable to process empty list`，且可能留下空 PDF。若合成时出错，应修复问题后重新生成并检查文件。

## 查看进度与排查问题

### 日志说明

| 日志 | 含义 |
| --- | --- |
| `total_frames: ...` | 视频报告的原始总帧数，不是预计截图数量 |
| `FRAME_RATE 0.5` | 当前每秒采样 0.5 次 |
| `saving ...png` | 已保存一张截图 |
| `74 screenshots Captured!` | 本次提取结束，共保存 74 张；数字随视频变化 |
| `Time taken ...s` | 截图提取阶段耗时，不包含后续人工等待和 PDF 合成 |
| `Press y to continue and n to terminate` | 使用了 `--review`，等待人工输入 |
| `Pdf Created!`、`pdf saved at ...` | PDF 合成结束，后面显示输出位置 |

长时间没有出现新的 `saving` 不一定表示卡住：连续画面不满足下一张截图条件时，程序仍会继续采样。当前没有连续百分比进度条。

### 常见问题

| 现象 | 检查与处理 |
| --- | --- |
| 只有 PNG，没有新的 PDF | 检查提取是否结束；如果使用 `--review`，需要输入 `y`；也可使用上面的已有截图合成命令 |
| `EOFError: EOF when reading a line` | 通常是启用 `--review` 却没有可用的交互输入；自动执行时去掉该选项 |
| `numpy.core.multiarray failed to import` 或 `_ARRAY_API not found` | 检查是否用了错误的 Python 环境，以及 NumPy 是否升级到了 2；在项目环境中重新安装 `requirements.txt` |
| `No module named 'zxingcpp'` 或其他模块缺失 | 在当前 `.venv` 中安装更新后的依赖，不要只安装到系统 Python |
| `unable to open file ...` | 检查路径、引号、文件是否存在和可读，以及 OpenCV 是否支持该视频编码 |
| Windows 路径报 `WinError 123` | 当前版本已使用文件名解析修复反斜杠路径问题；确认运行的是本目录最新脚本，输入路径不要额外带入引号字符 |
| 裁剪参数报错 | 参数必须是非负整数，并满足“上裁 + 下裁 < 视频高度” |
| 标题或正文被裁掉 | 减少裁剪量；二维码可改用 `--remove-qr` 局部处理 |
| 去二维码后仍有文字、网址、Logo | 这些不属于二维码；可根据位置选择上下裁剪 |
| 二维码仍有残留 | 检查是否太小、模糊或残缺；当前检测不能保证识别所有二维码 |
| 截图数量仍多 | 当前不是全局去重；结合 `--review`、采样间隔及检测参数调整 |
| 截图太少或没有 PNG | 视频可能缺少稳定画面，或采样间隔、检测阈值不合适；先调参验证，不能把空目录直接合成 PDF |
| 视频到末尾仍持续运行 | 当前代码在帧数和帧率有效时限制到视频时长；确认已更新代码。元数据无效时仍依赖读取失败退出，需进一步检查输入视频 |
| 新命令报 `unrecognized arguments` | 确认当前脚本支持这些选项，运行 `--help` 检查；采样频率需要修改常量，不是命令行选项 |

## 测试与项目结构

运行全部测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

当前包含 21 项测试，覆盖：

- 默认无交互生成 PDF，以及人工检查的 `y` / `n` 分支。
- 每 2 秒采样、读取失败处理、末帧重复返回时的退出。
- Windows 绝对路径、多点文件名和 PDF 命名。
- 裁剪范围、非法参数、检测与保存使用相同的裁剪结果。
- 单个、多个、紧贴边缘的二维码处理，以及原图和正文区域保护。
- 真实小视频到 PNG/PDF 的命令行流程、裁剪与去二维码组合、原输出保留。

测试会在项目内创建临时测试文件，完成后自动清理，不会重跑或替换 `output` 中的业务结果。

项目结构：

```text
video2pdf/
├── video2pdfslides.py          # 视频读取、处理、截图和 PDF 生成
├── requirements.txt           # 依赖约束
├── README.md                  # 本文档
├── input/                     # 本地视频目录；视频不随 Git 提交，也可使用外部路径
├── output/                    # 当前工作目录下的默认输出位置
├── tests/
│   ├── test_video2pdfslides.py # 基础流程、采样、路径测试
│   ├── test_cropping.py       # 裁剪测试
│   └── test_qr_removal.py     # 二维码处理测试
└── .venv/                     # 本地 Python 环境，不属于源码功能
```

使用 uv 安装方案时，还可能存在 `.uv-python/` 和 `.uv-cache/`。

## 原项目信息

- 原项目演示：[YouTube demo](https://www.youtube.com/watch?v=Q0BIPYLoSBs)
- 原作者：kaushik jeyaraman
- 原作者联系方式：kaushikjjj@gmail.com

本目录在原有视频截图流程上加入了 Windows 路径兼容、依赖兼容约束、视频末尾退出、默认自动生成 PDF、可选人工检查、上下裁剪、二维码移除及对应测试。
