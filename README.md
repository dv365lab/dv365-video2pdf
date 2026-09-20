# video2pdfslides：视频截图与 PDF 生成工具

将本地视频或视频网址中的稳定画面保存为 PNG，并自动合成为 PDF。网址会先下载为本地视频。适合以幻灯片、文档、终端演示为主的视频。

这是一个 Python 命令行工具，没有网页界面。当前默认 **每 0.5 秒采样 1 次**，再按内容变化选择截图，并保留短停留页和动画最终状态；并不是每次采样都会输出一张图片。

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
| 按时间间隔采样 | 当前默认间隔 0.5 秒，可修改脚本中的 `FRAME_RATE` |
| 页面内容截图 | 比较相邻画面与已选页面，保留持续内容变化、转场前短页和最后一页 |
| PNG 输出 | 保留原视频宽度；未裁剪时保留原画面尺寸 |
| 自动生成 PDF | 提取结束后自动合成，无需输入 `y` |
| 可选人工检查 | `--review` 暂停流程，允许检查、删除多余截图后再合成 |
| 上下裁剪 | `--crop-top`、`--crop-bottom` 按像素移除页眉、页脚区域 |
| 二维码移除 | `--remove-qr` 检测二维码，并用附近背景色覆盖检测区域 |
| 组合处理 | 二维码移除、上下裁剪、人工检查可以一起使用 |
| Windows 路径 | 支持带盘符、反斜杠、空格的路径，保留 `1.1.mp4` 中的多点文件名 |
| 视频网址 | 支持 YouTube 等 yt-dlp 支持的视频页面及 HTTP(S) 视频直链，先下载再转换 |
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
| `opencv_python` | `4.5.2.52` | 视频读取、画面比较、图像处理和 PNG 保存 |
| `img2pdf` | `0.4.1` | 将 PNG 合成为 PDF |
| `numpy` | `<2` | 图像数组处理；兼容当前 OpenCV |
| `zxing-cpp` | `2.3.0` | 二维码识别与定位 |

Pillow、pikepdf 等间接依赖会随上述依赖安装。安装和下载网络视频需要联网，处理本地视频时不需要在线服务；二维码内容不会被打开或访问。网址功能另需下方说明的外部 `yt-dlp` 命令。

## 快速开始

### Windows 双击启动

已按上文配置好 `.venv` 后，双击项目根目录的 [start.bat](start.bat)：

1. 在命令窗口中粘贴视频路径或完整的 `http://` / `https://` 视频网址，也可以把本地视频文件拖入窗口，然后按回车；输入带双引号也可以。
2. 程序使用默认参数提取截图并生成 PDF，结果保存在项目的 `output/` 目录。
3. 运行结束或报错后窗口会保留，按回车键关闭；未输入路径直接回车可取消。

启动脚本会自动定位到项目目录。缺少 `.venv` 时会提示先完成环境安装。若需要裁剪、去二维码或人工检查，请使用下方的命令行参数。

更新项目代码后，请关闭更新前已经打开的启动窗口，再重新双击 `start.bat`。交互输入由 `launcher.py` 处理，避免 CMD 展开网址和路径中的特殊字符。

### 视频网址

双击 `start.bat` 后直接粘贴网址即可，也可以在命令行运行：

```powershell
.\.venv\Scripts\python.exe video2pdfslides.py "https://www.youtube.com/watch?v=视频ID"
.\.venv\Scripts\python.exe video2pdfslides.py "https://example.com/lesson.mp4" --crop-bottom 64 --remove-qr
```

请将示例替换成真实视频网址，命令行中的网址要加双引号，避免 `&` 等字符被终端解释。

网址下载优先使用 **项目内的 `.tools/yt-dlp.exe`**；未安装时才查找 PATH。下载器版本必须为 **2026.08.19 或更新版本**，较旧版本使用的 YouTube 客户端会遇到 HTTP 403（见 [官方修复记录](https://github.com/yt-dlp/yt-dlp/releases/tag/2026.08.19)）。截图仍使用项目的 Python 3.9。

本机已安装并验证项目内的 `2026.08.19`。首次配置或修复旧版本时，在项目目录运行：

```powershell
.\.venv\Scripts\python.exe setup_downloader.py
```

安装脚本从官方 GitHub 下载独立版，核对官方 SHA-256 后再替换项目内的下载器；失败会保留现有文件。该命令不会修改全局 Python 或 PATH。`setup_downloader.py` 的 `VERSION` 固定为本项目验证过的版本。

其他安装方式和依赖说明：

- 从 [yt-dlp 官方安装说明](https://github.com/yt-dlp/yt-dlp#installation) 下载 Windows 独立版 `yt-dlp.exe`，将所在目录加入 PATH。当前 yt-dlp Python 包要求 Python 3.10 以上，因此不在本项目 Python 3.9 的 `requirements.txt` 中安装。
- YouTube 需要 JavaScript 运行时，推荐按 [官方 EJS 说明](https://github.com/yt-dlp/yt-dlp/wiki/EJS) 安装 Deno。Windows 可以将 `deno.exe` 放在 `yt-dlp.exe` 同目录或加入 PATH；独立版 yt-dlp 已包含所需的 EJS 脚本。使用 pip 安装 yt-dlp 时，请在其兼容的 Python 环境中安装 `yt-dlp[default]`。
- 安装后重新打开启动窗口，运行 `.\.tools\yt-dlp.exe --version` 和 `deno --version` 检查。网站规则可能变化，下载失效时可按照官方说明更新项目内工具：`.\.tools\yt-dlp.exe -U`。程序会显示实际使用的下载器路径，并先检查所选视频格式能否下载。

下载的视频保存在 `input/downloads/`，文件名包含站点、视频 ID 和网址摘要，避免不同网址的同名视频相互覆盖。PDF 和 PNG 仍保存在 `output/`，使用下载文件的名称；裁剪和去二维码的后缀规则不变。下载文件会保留，重复运行同一网址通常会复用现有文件，但仍需联网解析网址。下载目录不随 Git 提交。

每次处理单个、非直播视频。带有播放列表参数的单视频链接只处理该视频；纯播放列表、直播和未开始的视频不支持。程序优先下载 H.264 格式的画面，不额外合并音轨；下载文件可能没有声音。部分站点或格式仍可能需要 FFmpeg，视频能否转换还取决于 OpenCV 的解码支持。

支持范围以 [yt-dlp 支持站点](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md) 为准，并非任意网页都能提取视频。需要登录、会员、地区限制或站点验证的视频可能下载失败。程序会显示下载器的错误，不会自动读取浏览器登录信息。下载失败或下载文件无法解码时，不会创建或清空截图目录。

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
| `video_path` | 是 | 无 | 本地视频路径或完整的 HTTP(S) 视频网址 |
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
FRAME_RATE = 2
```

`FRAME_RATE` 的单位是“每秒采样次数”，不是“每隔多少秒”。换算关系为：

```text
采样间隔（秒） = 1 / FRAME_RATE
```

| `FRAME_RATE` | 采样间隔 |
| --- | --- |
| `3` | 约 0.333 秒 |
| `2` | 0.5 秒，当前默认值 |
| `1.5` | 约 0.667 秒 |
| `1` | 1 秒 |
| `0.5` | 2 秒 |
| `0.25` | 4 秒 |

该值必须大于 0。当前代码没有为手动修改后的非法频率做额外校验。

**采样间隔加倍，最终截图数量不一定减半。** 每个采样点只是候选画面，脚本还会判断内容变化和稳定性。完整动画状态可能只停留不足 1 秒，较长的采样间隔可能直接跨过该状态。

旧背景模型曾在同一段约 37 分 49 秒的视频上得到：每秒 3 次为 132 张，每秒 1.5 次为 110 张，每秒 0.5 次为 74 张。这些历史结果不代表当前内容比较算法，也不是数量换算公式。裁剪或移除二维码仍可能改变最终张数。

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
  → 按采样间隔读取帧（默认目标时刻为 0、0.5、1……秒）
  → 可选：检测并覆盖二维码
  → 可选：裁掉顶部和底部
  → 保留当前尺寸作为待保存画面
  → 另行缩放到宽 600 像素，转灰度并轻微模糊后比较内容
  → 稳定且内容持续改变时选为新页；大幅转场前补选短停留页，连续大幅运动不逐帧补选
  → 保留较晚的小幅动画更新，选下一页时写出上一候选
  → 到达视频结束位置，补选最后候选并保存
  → 可选：--review 人工检查
  → 按 PNG 文件名排序，合成 PDF
```

宽 600 像素的缩图只用于分析。输出 PNG 保留原视频宽度及裁剪后的高度，不会因为分析缩放而变成宽 600 像素。

### 脚本常量

以下参数均位于 `video2pdfslides.py` 顶部，需要修改代码后重新运行：

| 常量 | 当前值或表达式 | 作用 |
| --- | --- | --- |
| `OUTPUT_SLIDES_DIR` | `"./output"` | 输出根目录，相对于当前工作目录 |
| `FRAME_RATE` | `2` | 每秒采样次数 |
| `PIXEL_CHANGE_THRESHOLD` | `20` | 灰度差超过此值才计为改变，抑制轻微压缩噪声 |
| `STABLE_CHANGE_PERCENT` | `0.4` | 相邻采样变化比例不超过此值时认为足够稳定 |
| `PAGE_CHANGE_PERCENT` | `0.1` | 相对固定已选页基准的持续内容变化比例，达到此值可选新页 |
| `SCENE_CHANGE_PERCENT` | `3` | 相邻变化达到此值时，在转场前补选上一帧；不再是选新页的必要门槛 |

持续变化要求相对已选页的改变在相邻两次采样中都存在；选择稳定页时还使用最近三次分析图的中值作为比较基准，减轻移动光标的影响。输出仍是原始采样画面，不是中值合成图。小于选页阈值的最终动画更新会更新待保存原图，但不会重置比较基准，因此小变化可以累积。

### 去重方式与调参

当前检测不是全局去重。返回之前讲过的页面会按时间顺序再次保留。为减少漏页，转场前候选和逐步揭示的内容可能形成多张截图；少量转场帧也可能保留，需要精简时使用 `--review`。

- **截图较多：** 提高 `PAGE_CHANGE_PERCENT` 可以减少局部更新，但也可能漏掉小字变化，建议先人工检查。
- **动画或移动指针导致不稳定：** 可适当提高 `STABLE_CHANGE_PERCENT`，但可能保留更多运动画面。
- **短暂完整页被跨过：** 提高 `FRAME_RATE`。短于采样间隔、持续滚动或画面模糊的内容仍无法保证完整提取。

三个以 `PERCENT` 结尾的参数单位都是百分数，例如 `0.1` 表示 0.1%，不是 10%。

旧版使用 MOG2 背景模型，动画残留可能长时间超过稳定阈值，小幅文字更新又达不到 3% 的重新截图门槛。这曾导致示例 YouTube 视频开场及中段至少 12 个 PPT 主题遗漏；当前实现已移除该门槛和背景学习依赖。

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

示例：`0003_1.47.png`。

- `0003`：截图序号，从 `0000` 开始，至少补齐 4 位。
- `1.47`：分钟数的小数形式，四舍五入到两位小数，不是“1 分 47 秒”。
- 时间来自实际采样时刻，不再额外偏移一个采样间隔；但分钟数舍入后仍只适合粗略定位。

PDF 使用截图目录中 `*.png` 的文件名字典序。删除多余截图后无需重新编号，但任意改名可能改变顺序。四位补零能保持前 10000 张截图的顺序；超过此数量需额外检查。

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
| `FRAME_RATE 2` | 当前每秒采样 2 次 |
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
| `未找到 yt-dlp`、版本过旧或无法启动 | 运行 `.\.venv\Scripts\python.exe setup_downloader.py` 安装项目下载器，再重新打开启动窗口 |
| `视频下载失败` | 查看上方下载器错误，检查网络、网址可用性及 yt-dlp 版本；登录、会员或地区限制内容可能不可下载 |
| `'O_PATH' is not recognized` | 关闭更新前打开的旧窗口，重新双击当前 `start.bat`；新版已将交互迁移到 Python |

## 测试与项目结构

运行全部测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试覆盖：

- Windows 双击启动的路径输入、工作目录定位、取消与报错，以及真实视频转换。
- 网址及查询参数传递、下载失败处理、原有输出保护；安装 yt-dlp 时还会验证本地 HTTP 视频下载到 PDF 的完整流程。
- 启动期间更新脚本的回归、项目内下载器优先级与最低版本、安装包校验和失败时保留旧工具。
- 默认无交互生成 PDF，以及人工检查的 `y` / `n` 分支。
- 每 0.5 秒采样、真实采样时间、读取失败处理、末帧重复返回时的退出。
- 首尾短页、单采样视频、相似文字更新、动画最终状态、移动指针和返回旧页。
- Windows 绝对路径、多点文件名和 PDF 命名。
- 裁剪范围、非法参数、检测与保存使用相同的裁剪结果。
- 单个、多个、紧贴边缘的二维码处理，以及原图和正文区域保护。
- 真实小视频到 PNG/PDF 的命令行流程、裁剪与去二维码组合、原输出保留。

测试会在项目内创建临时测试文件，完成后自动清理，不会重跑或替换 `output` 中的业务结果。

项目结构：

```text
video2pdf/
├── start.bat                   # Windows 双击启动，输入视频路径或网址后转换
├── launcher.py                 # 中文交互与安全传递视频路径、网址
├── setup_downloader.py         # 校验并安装项目内下载器
├── video2pdfslides.py          # 视频读取、处理、截图和 PDF 生成
├── requirements.txt           # 依赖约束
├── README.md                  # 本文档
├── input/                     # 本地视频目录；视频不随 Git 提交，也可使用外部路径
├── output/                    # 当前工作目录下的默认输出位置
├── tests/
│   ├── test_video2pdfslides.py # 基础流程、采样、路径测试
│   ├── test_cropping.py       # 裁剪测试
│   ├── test_qr_removal.py     # 二维码处理测试
│   ├── test_start_bat.py      # Windows 启动脚本测试
│   ├── test_video_urls.py     # 网址识别、下载与错误处理测试
│   ├── test_url_download_integration.py # HTTP 下载到 PDF 的集成测试
│   ├── test_downloader_selection.py # 下载器选择与版本检查
│   ├── test_setup_downloader.py # 安装校验与失败保护
│   ├── test_slide_detection.py # 漏页回归
│   └── test_real_slide_coverage.py # 本地示例视频的12个缺页主题回归
└── .venv/                     # 本地 Python 环境，不属于源码功能
```

使用 uv 安装方案时，还可能存在 `.uv-python/` 和 `.uv-cache/`。

## 原项目信息

- 原项目演示：[YouTube demo](https://www.youtube.com/watch?v=Q0BIPYLoSBs)
- 原作者：kaushik jeyaraman
- 原作者联系方式：kaushikjjj@gmail.com

本目录在原有视频截图流程上加入了 Windows 路径兼容、依赖兼容约束、视频末尾退出、默认自动生成 PDF、可选人工检查、上下裁剪、二维码移除及对应测试。
